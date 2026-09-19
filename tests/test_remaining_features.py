from __future__ import annotations

# ruff: noqa: E501 -- compact OOXML fixture strings are intentionally unwrapped.
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, text

from app.corpus.resource_parsers import DOCX_MEDIA_TYPE, parse_resource
from app.help.routing import HelpResourceService
from app.hpd.connector import (
    BuildingCandidate,
    HpdViolationRecord,
    PropertyQuery,
    PropertySearchResponse,
)
from app.local_app import create_local_app
from app.localization.catalog import load_locale_catalog
from app.offline.service import OfflineExtensionService
from app.research.comparisons import SourceComparisonService
from app.research.dossiers import PropertyDossierService
from app.research.matters import MatterConflict, MatterService
from app.storage.database import LocalStorage
from app.storage.migrations import migrate_workspace, migration_preflight
from app.storage.schema import chunks, documents, source_modules, source_versions
from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied, NetworkPolicy


def _workspace(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    return context, LocalStorage.open(context.paths, initialize=True)


def test_matters_save_immutable_payload_idempotently_and_export(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    try:
        service = MatterService(storage)
        matter = service.create(
            "Heat case", description="Winter conditions", tags=["heat", "tenant"]
        )
        payload = {
            "question": "What heat is required?",
            "answer": "Review the source [E1].",
            "evidence": [
                {
                    "chunk_id": "chunk-1",
                    "source_version_id": "version-1",
                    "excerpt": "Temperature requirement.",
                }
            ],
        }
        first = service.save_payload(
            matter["id"],
            kind="answer",
            payload=payload,
            source_identity="answer-job:one",
            idempotency_key="save-one",
            original_operation_id="one",
        )
        repeated = service.save_payload(
            matter["id"],
            kind="answer",
            payload=payload,
            source_identity="answer-job:one",
            idempotency_key="save-one",
            original_operation_id="one",
        )
        assert repeated["id"] == first["id"]
        assert service.get(matter["id"])["item_count"] == 1
        assert service.list(query="Temperature")[0]["id"] == matter["id"]

        second_matter = service.create("Shared evidence")
        service.link(second_matter["id"], first["id"])
        unlink_preview = service.delete_item(first["id"], matter_id=second_matter["id"])
        assert unlink_preview["saved_item_will_be_deleted"] is False
        assert service.get(second_matter["id"])["item_count"] == 1
        unlinked = service.delete_item(
            first["id"], matter_id=second_matter["id"], apply=True
        )
        assert unlinked["status"] == "unlinked"
        assert service.get(second_matter["id"])["item_count"] == 0
        assert service.get_item(first["id"])["payload_hash"] == first["payload_hash"]

        note = service.put_note(matter_id=matter["id"], body="Call history")
        with pytest.raises(MatterConflict, match="refresh and retry"):
            service.put_note(
                note_id=note["id"],
                matter_id=second_matter["id"],
                body="Wrong notebook",
                expected_revision=note["revision"],
            )

        with pytest.raises(MatterConflict, match="another result"):
            service.save_payload(
                matter["id"],
                kind="answer",
                payload={"answer": "different"},
                source_identity="answer-job:two",
                idempotency_key="save-one",
            )
        archive = service.export(matter["id"], context.paths.exports)
        with zipfile.ZipFile(archive) as exported:
            manifest = json.loads(exported.read("manifest.json"))
            for name, metadata in manifest["files"].items():
                assert (
                    hashlib.sha256(exported.read(name)).hexdigest()
                    == metadata["sha256"]
                )
    finally:
        storage.close()


def test_source_comparison_classifies_changes_and_finds_saved_impacts(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        module_id = "00000000-0000-0000-0000-000000000001"
        old_version = "00000000-0000-0000-0000-000000000011"
        new_version = "00000000-0000-0000-0000-000000000012"
        now = datetime.now(UTC)
        with storage.corpus_engine.begin() as connection:
            connection.execute(
                insert(source_modules).values(
                    id=module_id,
                    slug="comparison-fixture",
                    name="Comparison fixture",
                    source_type="law",
                    publisher="Fixture publisher",
                    jurisdiction="NYC",
                    source_url="https://example.test/source",
                    scope_json="{}",
                    manifest_json="{}",
                    origin="core",
                    acquisition_kind="managed_download",
                    model_use_allowed=True,
                    enabled=True,
                )
            )
            for version_id, content_hash, parser in (
                (old_version, "a" * 64, "parser-v1"),
                (new_version, "b" * 64, "parser-v1"),
            ):
                connection.execute(
                    insert(source_versions).values(
                        id=version_id,
                        source_module_id=module_id,
                        content_hash=content_hash,
                        parser_version=parser,
                        artifact_uri="artifacts/fixture.txt",
                        retrieved_at=now,
                        last_checked_at=now,
                        validation_state="validated",
                        validation_json="{}",
                        provenance_json="{}",
                    )
                )
                document_id = f"doc-{version_id[-2:]}"
                connection.execute(
                    insert(documents).values(
                        id=document_id,
                        source_version_id=version_id,
                        stable_id="law",
                        title="Fixture law",
                        source_url="https://example.test/source",
                    )
                )
                body = (
                    "Owner must provide heat."
                    if version_id == old_version
                    else "Owner must provide heat and hot water."
                )
                connection.execute(
                    insert(chunks).values(
                        id=f"chunk-{version_id[-2:]}",
                        document_id=document_id,
                        source_module_id=module_id,
                        source_version_id=version_id,
                        stable_id="section-1",
                        citation="Fixture § 1",
                        title="Heat",
                        text=body,
                        text_hash=hashlib.sha256(body.encode()).hexdigest(),
                        locator_json='{"section":"1"}',
                    )
                )
                split_parts = (
                    [("section-2", "Tenant must pay rent.")]
                    if version_id == old_version
                    else [
                        ("section-2-a", "Tenant must"),
                        ("section-2-b", "pay rent."),
                    ]
                )
                for index, (stable_id, text_body) in enumerate(split_parts):
                    connection.execute(
                        insert(chunks).values(
                            id=f"chunk-split-{version_id[-2:]}-{index}",
                            document_id=document_id,
                            source_module_id=module_id,
                            source_version_id=version_id,
                            stable_id=stable_id,
                            citation="Fixture § 2",
                            title="Rent",
                            text=text_body,
                            text_hash=hashlib.sha256(text_body.encode()).hexdigest(),
                            locator_json=json.dumps(
                                {"section": "2", "part": index + 1}
                            ),
                        )
                    )
                renumbered_citation = (
                    "Fixture § 3" if version_id == old_version else "Fixture § 4"
                )
                connection.execute(
                    insert(chunks).values(
                        id=f"chunk-renumber-{version_id[-2:]}",
                        document_id=document_id,
                        source_module_id=module_id,
                        source_version_id=version_id,
                        stable_id=f"section-{renumbered_citation[-1]}",
                        citation=renumbered_citation,
                        title="Notice",
                        text="Notice is required.",
                        text_hash=hashlib.sha256(b"Notice is required.").hexdigest(),
                        locator_json=json.dumps({"section": renumbered_citation[-1]}),
                    )
                )
        matters_service = MatterService(storage)
        matter = matters_service.create("Affected research")
        matters_service.save_payload(
            matter["id"],
            kind="answer",
            payload={"evidence": [{"chunk_id": "chunk-11"}]},
            source_identity="answer:fixture",
            idempotency_key="impact-fixture",
        )

        result = SourceComparisonService(storage).create(
            "comparison-fixture", old_version, new_version
        )
        assert result["counts"] == {
            "metadata_only": 1,
            "text_changed": 1,
            "uncertain": 2,
        }
        text_change = next(
            change
            for change in result["changes"]
            if change["classification"] == "text_changed"
        )
        assert "hot water" in text_change["diff"]
        assert (
            sum(change["classification"] == "uncertain" for change in result["changes"])
            == 2
        )
        assert not any(
            change["classification"] == "text_changed"
            and change["canonical_key"] == "citation:fixture § 2"
            for change in result["changes"]
        )
        assert result["saved_research_impacts"][0]["matching_chunk_ids"] == ["chunk-11"]
    finally:
        storage.close()


def test_property_dossier_keeps_identity_panels_and_typed_timeline(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        now = datetime.now(UTC)
        response = PropertySearchResponse(
            query=PropertyQuery(building_id="123", limit=50),
            candidates=(
                BuildingCandidate(
                    building_id="123",
                    registration_id="456",
                    borough="MANHATTAN",
                    house_number="10",
                    street_name="MAIN STREET",
                    zip_code="10001",
                ),
            ),
            records=(
                HpdViolationRecord(
                    violation_id="V1",
                    building_id="123",
                    registration_id="456",
                    borough="MANHATTAN",
                    house_number="10",
                    street_name="MAIN STREET",
                    zip_code="10001",
                    apartment="2A",
                    violation_class="C",
                    inspection_date="2026-01-02T00:00:00.000",
                    approved_date=None,
                    certified_date=None,
                    order_number=None,
                    nov_id=None,
                    description="No heat",
                    current_status="Open",
                    current_status_date="2026-01-03T00:00:00.000",
                    violation_status="Open",
                ),
            ),
            requires_selection=False,
            continuation=None,
            is_complete=True,
            returned_count=1,
            fetched_at=now,
            dataset_id="wvxf-dwi5",
            dataset_url="https://data.cityofnewyork.us/resource/wvxf-dwi5",
            connector_version="fixture-v1",
            source_status="success",
            total_count=1,
        )
        service = PropertyDossierService(storage)
        identity = service.resolve(response)
        dossier = service.create_observation(
            identity["id"], response, panels=["hpd_violations", "dob_permits"]
        )
        panels = dossier["payload"]["panels"]
        assert panels["hpd_violations"]["status"] == "complete"
        assert panels["dob_permits"]["reason_code"] == "dataset_adapter_not_verified"
        assert {event["date_type"] for event in dossier["payload"]["timeline"]} == {
            "inspection_date",
            "current_status_date",
        }
    finally:
        storage.close()


def test_docx_extracts_structure_final_view_and_reports_unsafe_features() -> None:
    content = _docx_fixture()
    parsed = parse_resource(
        content,
        filename="lease.docx",
        media_type=DOCX_MEDIA_TYPE,
        title="Lease",
    )
    assert parsed.media_type == DOCX_MEDIA_TYPE
    assert any(chunk.locator["kind"] == "docx_table_cell" for chunk in parsed.chunks)
    assert any(chunk.locator["kind"] == "docx_footnote" for chunk in parsed.chunks)
    combined = " ".join(chunk.text for chunk in parsed.chunks)
    assert "Inserted final text" in combined
    assert "Deleted draft text" not in combined
    assert any("Tracked revisions" in warning for warning in parsed.warnings)
    assert any("comments" in warning for warning in parsed.warnings)
    assert any("External DOCX" in warning for warning in parsed.warnings)


def test_help_routing_localization_and_exact_loopback_policy(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        help_result = HelpResourceService(storage).match(
            "My landlord changed my locks and I cannot get back in.",
            language="en",
        )
        assert "nyc-lockout-en" in help_result["suggested_card_ids"]
        assert help_result["eligibility_determined"] is False
        historical = HelpResourceService(storage).match(
            "I am researching the history of lockouts.", language="en"
        )
        assert all(
            item["urgency"] == "related_resource"
            for item in historical["matched_rules"]
        )
        english = load_locale_catalog("en")
        spanish = load_locale_catalog("es")
        assert set(english["messages"]) == set(spanish["messages"])

        policy = NetworkPolicy(
            offline=True,
            allow_loopback_services=True,
            allowed_loopback_urls=("http://127.0.0.1:9000/v1/responses",),
        )
        policy.assert_url_allowed(
            "http://127.0.0.1:9000/v1/responses", purpose="local model"
        )
        with pytest.raises(NetworkAccessDenied, match="selected local endpoint"):
            policy.assert_url_allowed(
                "http://127.0.0.1:9000/v1/embeddings", purpose="local model"
            )
    finally:
        storage.close()


def test_offline_readiness_does_not_claim_unverified_extensions(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    try:
        readiness = OfflineExtensionService(context, storage).readiness()
        assert readiness["no_remote_fallback"] is True
        assert readiness["capabilities"]["local_cited_answers"]["state"] == "blocked"
        assert readiness["capabilities"]["offline_hpd_snapshot"]["state"] == "blocked"
        assert readiness["fully_offline_research_ready"] is False
    finally:
        storage.close()


def test_state_v1_to_v2_migration_adds_research_tables(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    new_tables = (
        "comparison_reviews",
        "comparison_changes",
        "source_comparisons",
        "matter_notes",
        "matter_items",
        "save_receipts",
        "saved_items",
        "matters",
        "dossier_observations",
        "property_identities",
        "extraction_runs",
        "help_feedback",
        "extension_installations",
    )
    try:
        with storage.state_engine.begin() as connection:
            connection.execute(text("DROP TABLE matter_fts"))
            for table_name in new_tables:
                connection.exec_driver_sql(f'DROP TABLE IF EXISTS "{table_name}"')
            connection.execute(
                text("UPDATE schema_metadata SET value='1' WHERE key='version'")
            )
    finally:
        storage.close()

    preflight = migration_preflight(context)
    assert preflight["status"] == "migration_available"
    migrated = migrate_workspace(context)
    assert migrated.from_state_version == 1
    assert migrated.to_state_version == 2
    storage = LocalStorage.open(context.paths)
    try:
        assert storage.versions() == {"corpus": 2, "state": 2}
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT 1 FROM sqlite_master WHERE name='matters'")
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT 1 FROM sqlite_master WHERE name='matter_fts'")
                )
                == 1
            )
    finally:
        storage.close()


def test_remaining_feature_routes_share_local_session_boundary(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    storage.close()
    app = create_local_app(context)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        locale = client.get("/api/v1/locales/es")
        assert locale.status_code == 200
        assert locale.json()["locale"] == "es"

        session = client.post(
            "/api/v1/session/exchange",
            headers={"Origin": "http://127.0.0.1"},
            json={"launch_token": app.state.launch_token},
        )
        headers = {
            "Origin": "http://127.0.0.1",
            "X-CSRF-Token": session.json()["csrf_token"],
        }
        matter = client.post(
            "/api/v1/matters",
            headers=headers,
            json={"title": "API matter", "tags": ["fixture"]},
        )
        assert matter.status_code == 201
        assert client.get("/api/v1/matters").json()["matters"][0]["item_count"] == 0

        help_match = client.post(
            "/api/v1/help-resources/match",
            headers=headers,
            json={"question": "I have no heat right now.", "language": "en"},
        )
        assert help_match.status_code == 200
        assert "nyc-essential-services-en" in help_match.json()["suggested_card_ids"]

        readiness = client.get("/api/v1/offline/readiness")
        assert readiness.status_code == 200
        assert readiness.json()["no_remote_fallback"] is True

        page = client.get("/").text
        assert 'data-view="matters"' in page
        assert ".docx" in page


def _docx_fixture() -> bytes:
    word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    document = f"""
    <w:document xmlns:w="{word_ns}"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Lease Terms</w:t></w:r></w:p>
      <w:p><w:r><w:t>Visible paragraph.</w:t></w:r><w:ins><w:r><w:t> Inserted final text.</w:t></w:r></w:ins><w:del><w:r><w:delText> Deleted draft text.</w:delText></w:r></w:del></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Rent</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>$1,500</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>
    """.encode()
    styles = f"""
    <w:styles xmlns:w="{word_ns}"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="Heading 1"/></w:style></w:styles>
    """.encode()
    footnotes = f"""
    <w:footnotes xmlns:w="{word_ns}"><w:footnote w:id="1"><w:p><w:r><w:t>Footnote text.</w:t></w:r></w:p></w:footnote></w:footnotes>
    """.encode()
    relationships = b"""
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId9" Type="template" Target="https://example.test/template" TargetMode="External"/></Relationships>
    """
    content_types = b"""
    <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>
    """
    output = BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/footnotes.xml", footnotes)
        archive.writestr("word/comments.xml", f'<w:comments xmlns:w="{word_ns}"/>')
        archive.writestr("word/_rels/document.xml.rels", relationships)
    return output.getvalue()
