from app.core.config import get_settings
from app.db.session import SessionLocal
from app.ingestion.artifacts import artifact_exists, read_artifact, write_artifact
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.registry import seed_sources
from app.models.source import Source
from app.models.source_version import SourceVersion


def test_hash_bytes_is_deterministic():
    assert hash_bytes(b"abc") == hash_bytes(b"abc")
    assert hash_bytes(b"abc") != hash_bytes(b"abcd")


def test_artifact_write_stays_inside_artifact_root(tmp_path):
    settings = get_settings()
    settings.artifact_storage_path = str(tmp_path)
    content = b"hello"
    content_hash = hash_bytes(content)

    artifact_uri = write_artifact("hpd-violations", content_hash, content, "json")

    assert artifact_exists(artifact_uri)
    assert read_artifact(artifact_uri) == content
    assert str(tmp_path.resolve()) in artifact_uri


def test_duplicate_source_versions_are_reused(tmp_path):
    settings = get_settings()
    settings.artifact_storage_path = str(tmp_path)
    content = b"source text"
    artifact = DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type="text/plain",
        byte_size=len(content),
        extension="txt",
        source_url="https://example.com/source.txt",
    )
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter(Source.slug == "hpd-guidance").one()

        first = create_or_get_source_version(db, source, artifact)
        second = create_or_get_source_version(db, source, artifact)

        assert first.id == second.id
        assert db.query(SourceVersion).count() == 1

