import csv
import json
from datetime import UTC, datetime

from app.exporting.service import ResearchExporter
from app.hpd.connector import HpdViolationRecord, PropertyQuery, PropertySearchResponse
from app.workspace.paths import resolve_workspace_paths


def test_answer_markdown_and_json_include_provenance(tmp_path) -> None:
    paths = resolve_workspace_paths(tmp_path / "workspace", environment={})
    paths.create()
    result = {
        "question": "What does the source say?",
        "status": "answered",
        "answer": "It says this [E1].",
        "generation_id": "generation-1",
        "answer_profile_id": "fake-answer-small",
        "embedding_profile_id": "fake-embedding-64",
        "retrieval_method": "exact+keyword+vector",
        "prompt_version": "local-answer-v1",
        "disclaimer": "Legal information, not legal advice.",
        "evidence": [
            {
                "marker": "E1",
                "chunk_id": "chunk-1",
                "citation": "HMC § 27-2005",
                "title": "Duties",
                "source_name": "Housing Maintenance Code",
                "source_url": "https://example.invalid/source",
                "publisher": "NYC Council",
                "retrieved_at": "2026-09-14T12:00:00+00:00",
                "last_checked_at": "2026-09-14T12:00:00+00:00",
                "effective_from": "2026-01-01T00:00:00+00:00",
                "effective_to": None,
                "excerpt": "Keep premises in good repair.",
            }
        ],
    }
    exporter = ResearchExporter(paths)
    markdown = exporter.answer(result, output_format="markdown")
    json_path = exporter.answer(result, output_format="json")

    assert "HMC § 27-2005" in markdown.read_text(encoding="utf-8")
    assert "generation-1" in markdown.read_text(encoding="utf-8")
    assert "Prompt version: local-answer-v1" in markdown.read_text(encoding="utf-8")
    assert "Publisher: NYC Council" in markdown.read_text(encoding="utf-8")
    assert (
        json.loads(json_path.read_text(encoding="utf-8"))["question"]
        == result["question"]
    )


def test_property_csv_labels_scope_and_neutralizes_formulas(tmp_path) -> None:
    paths = resolve_workspace_paths(tmp_path / "workspace", environment={})
    paths.create()
    record = HpdViolationRecord(
        violation_id="1",
        building_id="2",
        registration_id=None,
        borough="BROOKLYN",
        house_number="1",
        street_name="EXAMPLE STREET",
        zip_code="11201",
        apartment=None,
        violation_class="C",
        inspection_date="2026-01-01",
        approved_date=None,
        certified_date=None,
        order_number=None,
        nov_id=None,
        description='=HYPERLINK("bad")',
        current_status="OPEN",
        current_status_date=None,
        violation_status="Open",
    )
    response = PropertySearchResponse(
        query=PropertyQuery(building_id="2"),
        candidates=(),
        records=(record,),
        requires_selection=False,
        continuation="more",
        is_complete=False,
        returned_count=1,
        fetched_at=datetime(2026, 9, 14, tzinfo=UTC),
        dataset_id="wvxf-dwi5",
        dataset_url="https://data.cityofnewyork.us/example",
        connector_version="v1",
        source_status="success",
        has_more=True,
        next_cursor="more",
        fetch_started_at=datetime(2026, 9, 14, 11, 59, 59, tzinfo=UTC),
        fetch_completed_at=datetime(2026, 9, 14, tzinfo=UTC),
    )
    path = ResearchExporter(paths).property_csv(response)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["loaded_scope_complete"] == "False"
    assert rows[0]["export_version"] == "2"
    assert rows[0]["has_more"] == "True"
    assert rows[0]["fetch_started_at"].startswith("2026-09-14T11:59:59")
    assert rows[0]["description"].startswith("'=")
