from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from app.cli.ingest import (
    count_broad_guidance_chunks,
    download_source_command,
    ingest_artifact_command,
    ingest_missing_command,
    ingest_source_command,
    source_availability_command,
    verify_traceability_command,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ingestion.amlegal_xml import AMLEGAL_NYC_ADMIN_XML_ZIP_URL, HMC_XML_MEMBER
from app.ingestion.artifacts import artifact_exists
from app.ingestion.downloaders import DownloadedArtifact, hash_bytes
from app.ingestion.hpd_guidance import HpdGuidancePage, hpd_guidance_bundle_bytes
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion


def minimal_hmc_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<DOCUMENT>
  <LEVEL style-name="Chapter">
    <LEVEL style-name="Section">
      <RECORD id="section-27-2005">
        <HEADING>\xc2\xa7 27-2005 Duties of owner.</HEADING>
        <PARA>\xc2\xa7 27-2005 <CHARFORMAT>Duties of owner.</CHARFORMAT></PARA>
      </RECORD>
      <LEVEL style-name="Normal Level">
        <RECORD>
          <PARA>The owner shall keep the premises in good repair.</PARA>
        </RECORD>
      </LEVEL>
    </LEVEL>
    <LEVEL style-name="Section">
      <RECORD id="section-27-2029">
        <HEADING>\xc2\xa7 27-2029 Heat required.</HEADING>
        <PARA>\xc2\xa7 27-2029 <CHARFORMAT>Heat required.</CHARFORMAT></PARA>
      </RECORD>
      <LEVEL style-name="Normal Level">
        <RECORD>
          <PARA>Owners must provide heat during the heat season.</PARA>
        </RECORD>
      </LEVEL>
    </LEVEL>
  </LEVEL>
</DOCUMENT>
"""


def minimal_hmc_zip(member_name: str = HMC_XML_MEMBER) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr(member_name, minimal_hmc_xml())
    return buffer.getvalue()


def minimal_hpd_guidance_bundle() -> bytes:
    return hpd_guidance_bundle_bytes(
        [
            HpdGuidancePage(
                url=(
                    "https://www.nyc.gov/site/hpd/services-and-information/"
                    "heat-and-hot-water.page"
                ),
                title="Heat and Hot Water",
                html="""
                <html><head><title>Heat and Hot Water - HPD</title></head>
                <body><main>
                  <h1>Heat and Hot Water</h1>
                  <p>Owners must provide heat and hot water.</p>
                  <h2>Report a Complaint</h2>
                  <p>Tenants can report heat complaints to 311.</p>
                </main></body></html>
                """,
            )
        ]
    )


def test_ingest_missing_warns_without_raising_for_blocked_source(monkeypatch, capsys):
    def fake_ingest_source(source_slug: str) -> None:
        if source_slug == "nyc-housing-maintenance-code":
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr("app.cli.ingest.ingest_source_command", fake_ingest_source)
    monkeypatch.setattr("app.cli.ingest.load_hpd_violations_command", lambda: None)

    ingest_missing_command()

    captured = capsys.readouterr()
    assert "Completed missing-source ingestion." in captured.out
    assert "Warning: some sources failed to ingest." in captured.err
    assert "nyc-housing-maintenance-code: 403 Forbidden" in captured.err


def test_source_availability_reports_hmc_bulk_xml(capsys):
    source_availability_command()

    captured = capsys.readouterr()
    assert "nyc-housing-maintenance-code: mode=bulk_xml automated=true" in captured.out
    assert "hpd-guidance: mode=hpd_guidance_bundle automated=true" in captured.out
    assert f"download_url: {AMLEGAL_NYC_ADMIN_XML_ZIP_URL}" in captured.out
    assert f"artifact_member: {HMC_XML_MEMBER}" in captured.out
    assert "ny-rpapl: mode=direct_http automated=true" in captured.out
    assert "hpd-violations: mode=public_api automated=true" in captured.out


def test_hmc_download_uses_bulk_xml_url(monkeypatch, tmp_path):
    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path / "artifacts")
    requested_urls: list[str] = []
    zip_content = minimal_hmc_zip()

    def fake_download_url(url: str):
        requested_urls.append(url)
        return DownloadedArtifact(
            content=zip_content,
            content_hash=hash_bytes(zip_content),
            content_type="application/zip",
            byte_size=len(zip_content),
            extension="zip",
            source_url=url,
        )

    monkeypatch.setattr("app.cli.ingest.download_url", fake_download_url)
    with SessionLocal() as db:
        seed_sources(db)

    source_version = download_source_command("nyc-housing-maintenance-code")

    assert requested_urls == [AMLEGAL_NYC_ADMIN_XML_ZIP_URL]
    assert source_version.content_type == "application/zip"
    assert source_version.source_url.endswith("NYCadmin/0-0-0-60027")


def test_ingest_source_command_parses_hmc_bulk_xml(monkeypatch, tmp_path):
    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path / "artifacts")
    zip_content = minimal_hmc_zip()

    def fake_download_url(url: str):
        return DownloadedArtifact(
            content=zip_content,
            content_hash=hash_bytes(zip_content),
            content_type="application/zip",
            byte_size=len(zip_content),
            extension="zip",
            source_url=url,
        )

    monkeypatch.setattr("app.cli.ingest.download_url", fake_download_url)
    with SessionLocal() as db:
        seed_sources(db)

    ingest_source_command("nyc-housing-maintenance-code")

    with SessionLocal() as db:
        source = db.query(Source).filter_by(slug="nyc-housing-maintenance-code").one()
        chunks = (
            db.query(Chunk)
            .filter_by(source_id=source.id)
            .order_by(Chunk.order_index)
            .all()
        )
        citation = (
            db.query(Citation)
            .filter_by(normalized_citation="NYC Admin Code § 27-2005")
            .one()
        )

    assert len(chunks) == 2
    assert chunks[0].citation == "NYC Admin Code § 27-2005"
    assert chunks[0].title == "Duties of owner."
    assert "good repair" in chunks[0].text
    assert citation.chunk_id == chunks[0].id


def test_ingest_source_command_parses_hpd_guidance_bundle(monkeypatch, tmp_path):
    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path / "artifacts")
    bundle_content = minimal_hpd_guidance_bundle()

    def fake_bundle(source_url: str, **_kwargs):
        return DownloadedArtifact(
            content=bundle_content,
            content_hash=hash_bytes(bundle_content),
            content_type="application/json",
            byte_size=len(bundle_content),
            extension="json",
            source_url=source_url,
        )

    monkeypatch.setattr("app.cli.ingest.download_hpd_guidance_bundle", fake_bundle)
    with SessionLocal() as db:
        seed_sources(db)

    ingest_source_command("hpd-guidance")

    with SessionLocal() as db:
        source = db.query(Source).filter_by(slug="hpd-guidance").one()
        documents = db.query(Document).filter_by(source_id=source.id).all()
        chunks = (
            db.query(Chunk)
            .filter_by(source_id=source.id)
            .order_by(Chunk.order_index)
            .all()
        )

    assert len(documents) == 1
    assert len(chunks) == 2
    assert chunks[0].chunk_type == "guidance_section"
    assert chunks[0].citation is None
    assert "heat and hot water" in chunks[0].text.lower()


def test_ingest_artifact_command_creates_traceable_hmc_chunks(tmp_path):
    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path / "artifacts")
    official_url = (
        "https://codelibrary.amlegal.com/codes/newyorkcity/latest/"
        "NYCadmin/0-0-0-60027"
    )
    artifact_path = tmp_path / "hmc.html"
    artifact_path.write_text(Path("tests/fixtures/hmc_sample.html").read_text())
    with SessionLocal() as db:
        seed_sources(db)

    ingest_artifact_command(
        "nyc-housing-maintenance-code",
        str(artifact_path),
        official_url,
        "text/html",
    )

    with SessionLocal() as db:
        source = (
            db.query(Source)
            .filter(Source.slug == "nyc-housing-maintenance-code")
            .one()
        )
        source_version = db.query(SourceVersion).filter_by(source_id=source.id).one()
        chunks = (
            db.query(Chunk)
            .filter_by(source_id=source.id)
            .order_by(Chunk.order_index)
            .all()
        )
        citation = (
            db.query(Citation)
            .filter(Citation.normalized_citation == "NYC Admin Code § 27-2005")
            .one()
        )

        assert source_version.source_url == official_url
        assert artifact_exists(source_version.artifact_uri)
        assert len(chunks) == 2
        assert chunks[0].citation == "NYC Admin Code § 27-2005"
        assert citation.chunk_id == chunks[0].id


def test_ingest_missing_refreshes_source_with_missing_own_citations(monkeypatch):
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="ny-rpapl").one()
        source_version = SourceVersion(
            source_id=source.id,
            retrieved_at=datetime.now(UTC),
            source_url=source.source_url,
            content_hash="hash",
            artifact_uri="/tmp/rpapl.txt",
            content_type="text/plain",
            byte_size=10,
        )
        db.add(source_version)
        db.flush()
        document = Document(
            source_id=source.id,
            source_version_id=source_version.id,
            document_key="ny-rpapl",
            title=source.name,
            document_type="statute",
            jurisdiction=source.jurisdiction,
            source_url=source.source_url,
        )
        db.add(document)
        db.flush()
        db.add(
            Chunk(
                document_id=document.id,
                source_id=source.id,
                source_version_id=source_version.id,
                chunk_key="rpapl-711",
                chunk_type="section",
                citation="RPAPL § 711",
                title="Grounds where landlord-tenant relationship exists",
                text="§ 711. Grounds where landlord-tenant relationship exists.",
                text_hash="chunk-hash",
                order_index=0,
            )
        )
        db.commit()

    parsed_sources: list[str] = []
    monkeypatch.setattr("app.cli.ingest.ingest_source_command", lambda slug: None)
    monkeypatch.setattr("app.cli.ingest.load_hpd_violations_command", lambda: None)
    monkeypatch.setattr(
        "app.cli.ingest.parse_source_command",
        lambda slug: parsed_sources.append(slug),
    )

    ingest_missing_command()

    assert "ny-rpapl" in parsed_sources


def test_broad_guidance_detector_ignores_historical_versions():
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-guidance").one()
        old_version = SourceVersion(
            source_id=source.id,
            retrieved_at=datetime.now(UTC),
            source_url=source.source_url,
            content_hash="old-guidance-hash",
            artifact_uri="/tmp/old-hpd-guidance.html",
            content_type="text/html",
            byte_size=10,
            is_current=False,
        )
        current_version = SourceVersion(
            source_id=source.id,
            retrieved_at=datetime.now(UTC),
            source_url=source.source_url,
            content_hash="current-guidance-hash",
            artifact_uri="/tmp/current-hpd-guidance.json",
            content_type="application/json",
            byte_size=10,
            is_current=True,
        )
        db.add_all([old_version, current_version])
        db.flush()
        old_document = Document(
            source_id=source.id,
            source_version_id=old_version.id,
            document_key="old-hpd-guidance",
            title=source.name,
            document_type="guidance",
            jurisdiction=source.jurisdiction,
            source_url=source.source_url,
        )
        current_document = Document(
            source_id=source.id,
            source_version_id=current_version.id,
            document_key="current-hpd-guidance",
            title=source.name,
            document_type="guidance",
            jurisdiction=source.jurisdiction,
            source_url=source.source_url,
        )
        db.add_all([old_document, current_document])
        db.flush()
        db.add_all(
            [
                Chunk(
                    document_id=old_document.id,
                    source_id=source.id,
                    source_version_id=old_version.id,
                    chunk_key="old-full-text",
                    chunk_type="section",
                    citation=None,
                    title="Full Text",
                    text="Legacy broad HPD guidance text.",
                    text_hash="old-chunk-hash",
                    order_index=0,
                ),
                Chunk(
                    document_id=current_document.id,
                    source_id=source.id,
                    source_version_id=current_version.id,
                    chunk_key="current-guidance-section",
                    chunk_type="guidance_section",
                    citation=None,
                    title="Heat and Hot Water",
                    text="Structured current HPD guidance text.",
                    text_hash="current-chunk-hash",
                    order_index=0,
                ),
            ]
        )
        db.commit()

        assert count_broad_guidance_chunks(db, source.id) == 0

        db.add(
            Chunk(
                document_id=current_document.id,
                source_id=source.id,
                source_version_id=current_version.id,
                chunk_key="current-full-text",
                chunk_type="section",
                citation=None,
                title="Full Text",
                text="Current broad HPD guidance text.",
                text_hash="current-broad-chunk-hash",
                order_index=1,
            )
        )
        db.commit()

        assert count_broad_guidance_chunks(db, source.id) == 1


def test_verify_traceability_reports_guidance_without_citations_separately(
    monkeypatch,
    tmp_path,
    capsys,
):
    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path / "artifacts")
    bundle_content = minimal_hpd_guidance_bundle()

    def fake_bundle(source_url: str, **_kwargs):
        return DownloadedArtifact(
            content=bundle_content,
            content_hash=hash_bytes(bundle_content),
            content_type="application/json",
            byte_size=len(bundle_content),
            extension="json",
            source_url=source_url,
        )

    monkeypatch.setattr("app.cli.ingest.download_hpd_guidance_bundle", fake_bundle)
    with SessionLocal() as db:
        seed_sources(db)

    ingest_source_command("hpd-guidance")
    verify_traceability_command()

    captured = capsys.readouterr()
    assert "missing_artifacts: 0" in captured.out
    assert "law_chunks_missing_citation: 0" in captured.out
    assert "guidance_chunks_without_citation: 2" in captured.out
