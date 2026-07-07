from datetime import UTC, datetime
from pathlib import Path

from app.cli.ingest import ingest_artifact_command, ingest_missing_command
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ingestion.artifacts import artifact_exists
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion


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
