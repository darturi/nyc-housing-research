import json
from datetime import UTC, datetime

from app.cli import debug
from app.db.session import SessionLocal
from app.ingestion.downloaders import hash_bytes
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from tests.retrieval_fixtures import create_retrieval_corpus, create_test_user


def test_answer_debug_command_outputs_answer_and_retrieval_details(capsys):
    create_retrieval_corpus()
    create_test_user()

    debug.answer_debug_command(
        "admin@example.com",
        "NYC Admin Code § 27-2005",
        5,
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["provider"] == "fake"
    assert payload["model"] == "fake-answer-small"
    assert payload["answer_status"] == "answered"
    assert payload["cited_chunk_ids"]
    assert payload["source_coverage_present"] is True
    assert payload["disclaimer_present"] is True
    assert payload["retrieved_chunks"]
    first_chunk = payload["retrieved_chunks"][0]
    assert first_chunk["rank"] == 1
    assert first_chunk["chunk_id"]
    assert first_chunk["citation"]
    assert first_chunk["source_name"] == "NYC Housing Maintenance Code"
    assert first_chunk["text_excerpt"]


def test_answer_debug_command_outputs_unsupported_diagnostics(capsys):
    create_retrieval_corpus()
    create_test_user()

    debug.answer_debug_command(
        "admin@example.com",
        "What did the court hold in an unpublished housing case?",
        5,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_status"] == "unsupported"
    assert payload["cited_chunk_ids"] == []
    assert "does not contain enough" in payload["unsupported_reason"]
    assert "does not contain enough" in payload["answer"]
    assert payload["retrieved_chunks"]


def test_answer_debug_command_rejects_missing_user():
    try:
        debug.answer_debug_command(
            "missing@example.com",
            "NYC Admin Code § 27-2005",
            5,
        )
    except ValueError as exc:
        assert "User not found: missing@example.com" in str(exc)
    else:
        raise AssertionError("Expected missing user to raise ValueError")


def test_source_debug_command_reports_title_only_chunks(capsys):
    create_guidance_debug_rows()

    debug.source_debug_command("hpd-guidance", samples=5)

    payload = json.loads(capsys.readouterr().out)
    assert payload["source_slug"] == "hpd-guidance"
    assert payload["document_count"] == 1
    assert payload["chunk_count"] == 2
    assert payload["title_only_chunk_count"] == 1
    assert payload["text_length_min"] > 0
    assert payload["text_length_max"] > payload["text_length_min"]
    assert payload["sample_chunks"][0]["title_only"] is True
    assert payload["sample_chunks"][0]["title"] == "Report a Housing Complaint"


def test_source_debug_command_rejects_unknown_source():
    try:
        debug.source_debug_command("missing-source", 5)
    except ValueError as exc:
        assert "Unknown source slug: missing-source" in str(exc)
    else:
        raise AssertionError("Expected unknown source to raise ValueError")


def create_guidance_debug_rows() -> None:
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-guidance").one()
        source_version = SourceVersion(
            source_id=source.id,
            retrieved_at=datetime.now(UTC),
            source_url=source.source_url,
            content_hash="debug-guidance-hash",
            artifact_uri="/tmp/hpd-guidance.json",
            content_type="application/json",
            byte_size=10,
            is_current=True,
        )
        db.add(source_version)
        db.flush()
        document = Document(
            source_id=source.id,
            source_version_id=source_version.id,
            document_key="report-a-complaint",
            title="Report a Housing Complaint",
            document_type="guidance",
            jurisdiction=source.jurisdiction,
            source_url="https://www.nyc.gov/site/hpd/services-and-information/report-a-housing-complaint.page",
        )
        db.add(document)
        db.flush()
        title_only_text = "Report a Housing Complaint - HPD"
        substantive_text = (
            "Report a Housing Complaint\n"
            "Report a Quality or Safety Issue Learn how to file a housing "
            "quality or safety complaint to 311."
        )
        db.add_all(
            [
                Chunk(
                    document_id=document.id,
                    section_id=None,
                    source_id=source.id,
                    source_version_id=source_version.id,
                    chunk_key="title-only",
                    chunk_type="guidance_section",
                    citation=None,
                    title="Report a Housing Complaint",
                    text=title_only_text,
                    text_hash=hash_bytes(title_only_text.encode()),
                    order_index=0,
                ),
                Chunk(
                    document_id=document.id,
                    section_id=None,
                    source_id=source.id,
                    source_version_id=source_version.id,
                    chunk_key="substantive",
                    chunk_type="guidance_section",
                    citation=None,
                    title="Report a Housing Complaint",
                    text=substantive_text,
                    text_hash=hash_bytes(substantive_text.encode()),
                    order_index=1,
                ),
            ]
        )
        db.commit()
