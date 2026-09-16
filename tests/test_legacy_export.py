import hashlib
import sqlite3
from pathlib import Path

from app.corpus.bundle import CanonicalBundleService
from app.legacy.exporter import LegacyCorpusExporter
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


def test_read_only_legacy_export_round_trips_public_evidence(tmp_path: Path) -> None:
    artifact_root = tmp_path / "legacy-artifacts"
    artifact_root.mkdir()
    content = b"RPAPL section 711 fixture source"
    digest = hashlib.sha256(content).hexdigest()
    (artifact_root / "rpapl.txt").write_bytes(content)
    legacy_database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(legacy_database) as connection:
        connection.executescript(
            """
            CREATE TABLE sources (
              id TEXT PRIMARY KEY, slug TEXT, name TEXT, source_type TEXT,
              publisher TEXT, jurisdiction TEXT, source_url TEXT,
              access_type TEXT, license_status TEXT,
              redistribution_allowed BOOLEAN, notes TEXT, is_active BOOLEAN
            );
            CREATE TABLE source_versions (
              id TEXT PRIMARY KEY, source_id TEXT, retrieved_at TEXT,
              content_hash TEXT, artifact_uri TEXT, effective_start TEXT,
              effective_end TEXT, is_current BOOLEAN
            );
            CREATE TABLE documents (
              id TEXT PRIMARY KEY, source_id TEXT, source_version_id TEXT,
              document_key TEXT, title TEXT, source_url TEXT
            );
            CREATE TABLE chunks (
              id TEXT PRIMARY KEY, document_id TEXT, source_id TEXT,
              source_version_id TEXT, chunk_key TEXT, citation TEXT,
              title TEXT, text TEXT, text_hash TEXT
            );
            CREATE TABLE citations (
              id TEXT PRIMARY KEY, chunk_id TEXT, normalized_citation TEXT,
              citation_text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "source-1",
                "ny-rpapl",
                "New York RPAPL",
                "state_law",
                "New York State Senate",
                "New York",
                "https://example.test/rpapl",
                "public",
                "review_required",
                0,
                "fixture",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO source_versions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "version-1",
                "source-1",
                "2026-09-14T00:00:00+00:00",
                digest,
                "rpapl.txt",
                None,
                None,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
            (
                "document-1",
                "source-1",
                "version-1",
                "RPA",
                "RPAPL",
                "https://example.test/rpapl",
            ),
        )
        text = "A landlord may maintain a summary proceeding under section 711."
        connection.execute(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "chunk-1",
                "document-1",
                "source-1",
                "version-1",
                "711",
                "RPAPL § 711",
                "Section 711",
                text,
                hashlib.sha256(text.encode()).hexdigest(),
            ),
        )
        connection.execute(
            "INSERT INTO citations VALUES (?, ?, ?, ?)",
            ("citation-1", "chunk-1", "rpapl 711", "RPAPL § 711"),
        )

    bundle = tmp_path / "legacy-export.zip"
    summary = LegacyCorpusExporter(
        f"sqlite:///{legacy_database}", artifact_root
    ).export(bundle)
    assert summary.chunk_count == 1
    assert "excluded" in summary.vector_disposition

    context = WorkspaceContext.from_options(
        tmp_path / "restored", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    try:
        imported = CanonicalBundleService(storage).import_bundle(
            bundle, allow_partial=True
        )
        assert imported.chunk_count == 1
        assert imported.embedding_count == 0
    finally:
        storage.close()
