import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
from sqlalchemy import text

from app.corpus.service import CorpusService, SourceArtifact
from app.ingestion.amlegal_xml import HMC_XML_MEMBER
from app.retrieval.evaluation import evaluate_retrieval, load_retrieval_cases
from app.retrieval.local import LocalSearch
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


@pytest.fixture
def evaluation_corpus(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "evaluation", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    service = CorpusService(storage)
    # Frozen miniature documents independent of the question/answer labels.
    # Exercise production parsing, generation membership, citations, FTS,
    # query expansion, ranking, and filters. No providers or embeddings.
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/local_evaluation_corpus.json").read_text()
    )
    artifacts = []
    for slug, sections in fixture["sources"].items():
        content_type = "text/plain"
        if slug == "nyc-housing-maintenance-code":
            xml = (
                "<DOCUMENT>"
                + "".join(
                    '<LEVEL style-name="Section"><RECORD><HEADING>'
                    f"§ {escape(number)} {escape(section['title'])}</HEADING>"
                    f"<PARA>{escape(section['body'])}</PARA></RECORD></LEVEL>"
                    for number, section in sections.items()
                )
                + "</DOCUMENT>"
            )
            output = BytesIO()
            with ZipFile(output, "w") as archive:
                archive.writestr(HMC_XML_MEMBER, xml)
            content = output.getvalue()
            content_type = "application/zip"
        else:
            content = "\n\n".join(
                f"§ {number}. {section['title']}\n{section['body']}"
                for number, section in sections.items()
            ).encode()
        artifacts.append(
            SourceArtifact(
                slug=slug,
                content=content,
                content_type=content_type,
                source_url=service.manifests[slug].source_url,
                retrieved_at=datetime(2026, 9, 20, tzinfo=UTC),
            )
        )
    service.install_artifacts(artifacts, allow_partial=True)
    try:
        yield context, storage
    finally:
        storage.close()


def test_packaged_questions_run_against_real_local_retrieval(evaluation_corpus):
    _context, storage = evaluation_corpus
    cases = load_retrieval_cases()
    result = evaluate_retrieval(
        LocalSearch(storage), cases, k=5, minimum_recall=0.90, minimum_mrr=0.70
    )
    assert len(cases) >= 50
    assert result.passed, result
    assert result.corpus_generation_id == (
        CorpusService(storage).status().active_generation_id
    )
    assert all(case.hit for case in result.cases if case.case_id.startswith("scope-"))


def test_retrieval_gate_detects_a_missing_full_text_index(evaluation_corpus):
    _context, storage = evaluation_corpus
    with storage.corpus_engine.begin() as connection:
        connection.execute(text("DELETE FROM chunk_fts"))
    result = evaluate_retrieval(
        LocalSearch(storage),
        load_retrieval_cases(),
        minimum_recall=0.90,
        minimum_mrr=0.70,
    )
    assert not result.passed
    assert result.failures


def test_retrieval_evaluation_keeps_one_generation_during_source_update(
    evaluation_corpus,
):
    _context, storage = evaluation_corpus
    service = CorpusService(storage)
    original = service.status().active_generation_id

    class UpdatingSearch(LocalSearch):
        def search(self, *args, **kwargs):
            result = super().search(*args, **kwargs)
            if service.status().active_generation_id == original:
                service.install_artifacts(
                    [
                        SourceArtifact(
                            slug="ny-rpapl",
                            content=b"\xc2\xa7 711. Replacement fixture.",
                            source_url=service.manifests["ny-rpapl"].source_url,
                            content_type="text/plain",
                            retrieved_at=datetime.now(UTC),
                        )
                    ],
                    allow_partial=True,
                )
            return result

    cases = [
        {
            "id": str(index),
            "query": "statutory qualifications nonpayment",
            "expected_citations": ["RPAPL § 711"],
        }
        for index in range(2)
    ]
    result = evaluate_retrieval(UpdatingSearch(storage), cases, minimum_recall=1)
    assert result.passed
    assert result.corpus_generation_id == original
    assert service.status().active_generation_id != original


@pytest.mark.parametrize("threshold", [-1, 2, float("nan")])
def test_retrieval_gate_rejects_invalid_thresholds(evaluation_corpus, threshold):
    _context, storage = evaluation_corpus
    with pytest.raises(ValueError, match="thresholds"):
        evaluate_retrieval(
            LocalSearch(storage), load_retrieval_cases(), minimum_recall=threshold
        )
