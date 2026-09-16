from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine, insert, text
from sqlalchemy.pool import NullPool

from app.corpus.bundle import BundleSummary, CanonicalBundleService
from app.corpus.manifests import load_core_manifests
from app.corpus.service import CorpusService
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    citations,
    corpus_state,
    documents,
    generation_chunks,
    generation_sources,
    generations,
    source_modules,
    source_versions,
)
from app.workspace.paths import resolve_workspace_paths


class LegacyExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class LegacyExportSummary:
    bundle: BundleSummary
    destination: str
    vector_disposition: str
    source_count: int
    chunk_count: int
    transaction_mode: str


class LegacyCorpusExporter:
    """Copy the current public legal corpus without invoking legacy services.

    Database credentials are supplied by the caller and never persisted. The
    exporter issues only SELECT statements against legacy tables. Legacy vector
    preprocessing was not versioned, so vectors are deliberately excluded rather
    than being declared compatible with a local profile.
    """

    def __init__(self, database_url: str, artifact_root: Path) -> None:
        if not database_url:
            raise LegacyExportError("A legacy database URL is required.")
        self._database_url = database_url
        self._artifact_root = artifact_root.expanduser().resolve()

    def export(self, destination: Path) -> LegacyExportSummary:
        destination = destination.expanduser().resolve()
        engine = create_engine(self._database_url, poolclass=NullPool)
        temporary_root = Path(tempfile.mkdtemp(prefix="nyc-housing-legacy-export-"))
        transaction_mode = "database transaction"
        try:
            with engine.connect() as connection:
                transaction = connection.begin()
                try:
                    if connection.dialect.name == "postgresql":
                        connection.execute(text("SET TRANSACTION READ ONLY"))
                        transaction_mode = "PostgreSQL read-only transaction"
                    records = self._read_current_records(connection)
                    transaction.rollback()
                except Exception:
                    transaction.rollback()
                    raise
            summary = self._build_bundle(records, temporary_root, destination)
        except LegacyExportError:
            raise
        except Exception as exc:
            raise LegacyExportError(
                "The read-only legacy export could not be completed."
            ) from exc
        finally:
            engine.dispose()
            shutil.rmtree(temporary_root, ignore_errors=True)
        return LegacyExportSummary(
            bundle=summary,
            destination=str(destination),
            vector_disposition=(
                "excluded: legacy embedding preprocessing/profile provenance "
                "is not sufficient for compatibility"
            ),
            source_count=summary.source_count,
            chunk_count=summary.chunk_count,
            transaction_mode=transaction_mode,
        )

    def _read_current_records(self, connection) -> dict[str, list[dict]]:
        sources = _mappings(
            connection,
            """
            SELECT s.*, sv.id AS version_id, sv.retrieved_at,
                   sv.content_hash, sv.artifact_uri, sv.effective_start,
                   sv.effective_end
            FROM sources AS s
            JOIN source_versions AS sv ON sv.source_id = s.id
            WHERE s.is_active = true AND sv.is_current = true
              AND s.source_type <> 'hpd_dataset'
            ORDER BY s.slug, sv.retrieved_at DESC
            """,
        )
        if not sources:
            raise LegacyExportError("No current legal source versions were found.")
        core_slugs = set(load_core_manifests())
        sources = [row for row in sources if row.get("slug") in core_slugs]
        if not sources:
            raise LegacyExportError(
                "No current versions matching the supported core legal sources "
                "were found."
            )
        # Defensive deduplication if a legacy database contains multiple current
        # versions despite its intended invariant. The query is newest-first, so
        # retain the first version instead of overwriting it with an older row.
        by_source = {}
        for row in sources:
            by_source.setdefault(row["id"], row)
        version_ids = [row["version_id"] for row in by_source.values()]
        documents_rows = _selected_rows(
            connection, "documents", "source_version_id", version_ids
        )
        chunks_rows = _selected_rows(
            connection, "chunks", "source_version_id", version_ids
        )
        chunk_ids = [row["id"] for row in chunks_rows]
        citation_rows = _selected_rows(connection, "citations", "chunk_id", chunk_ids)
        return {
            "sources": list(by_source.values()),
            "documents": documents_rows,
            "chunks": chunks_rows,
            "citations": citation_rows,
        }

    def _build_bundle(
        self, records: dict[str, list[dict]], temporary_root: Path, destination: Path
    ) -> BundleSummary:
        paths = resolve_workspace_paths(
            temporary_root / "workspace",
            config_file=temporary_root / "workspace" / "settings.json",
            environment={},
        )
        paths.create()
        storage = LocalStorage.open(paths, initialize=True)
        try:
            generation_id = str(uuid.uuid4())
            now = datetime.now(UTC)
            core_slugs = set(load_core_manifests())
            selected_slugs = {row["slug"] for row in records["sources"]}
            missing = sorted(core_slugs - selected_slugs)
            with storage.corpus_engine.begin() as target:
                for source in records["sources"]:
                    artifact_uri = self._copy_artifact(source, paths.artifacts)
                    target.execute(
                        insert(source_modules).values(
                            id=source["id"],
                            slug=source["slug"],
                            name=source["name"],
                            source_type=source["source_type"],
                            publisher=source["publisher"],
                            jurisdiction=source["jurisdiction"],
                            source_url=source["source_url"],
                            scope_json=json.dumps(
                                {"description": source.get("notes") or "Legacy export"}
                            ),
                            manifest_json=json.dumps(
                                {
                                    "legacy_export": True,
                                    "access_type": source.get("access_type"),
                                    "license_status": source.get("license_status"),
                                    "redistribution_allowed": source.get(
                                        "redistribution_allowed"
                                    ),
                                },
                                sort_keys=True,
                            ),
                            enabled=True,
                        )
                    )
                    target.execute(
                        insert(source_versions).values(
                            id=source["version_id"],
                            source_module_id=source["id"],
                            content_hash=source["content_hash"],
                            parser_version="legacy-export-v1",
                            artifact_uri=artifact_uri,
                            retrieved_at=_datetime(source["retrieved_at"]),
                            last_checked_at=_datetime(source["retrieved_at"]),
                            effective_from=_datetime(source.get("effective_start")),
                            effective_to=_datetime(source.get("effective_end")),
                            validation_state="legacy_exported",
                            validation_json=json.dumps(
                                {
                                    "vector_disposition": (
                                        "excluded_unknown_preprocessing"
                                    )
                                },
                                sort_keys=True,
                            ),
                        )
                    )
                for document in records["documents"]:
                    target.execute(
                        insert(documents).values(
                            id=document["id"],
                            source_version_id=document["source_version_id"],
                            stable_id=document["document_key"],
                            title=document["title"],
                            source_url=document["source_url"],
                        )
                    )
                for chunk in records["chunks"]:
                    target.execute(
                        insert(chunks).values(
                            id=chunk["id"],
                            document_id=chunk["document_id"],
                            source_module_id=chunk["source_id"],
                            source_version_id=chunk["source_version_id"],
                            stable_id=chunk["chunk_key"],
                            citation=chunk.get("citation"),
                            title=chunk.get("title"),
                            text=chunk["text"],
                            text_hash=chunk["text_hash"],
                        )
                    )
                seen_citations: set[tuple[str, str]] = set()
                for citation in records["citations"]:
                    identity = (citation["chunk_id"], citation["normalized_citation"])
                    if identity in seen_citations:
                        continue
                    seen_citations.add(identity)
                    target.execute(
                        insert(citations).values(
                            id=citation["id"],
                            chunk_id=citation["chunk_id"],
                            normalized_citation=citation["normalized_citation"],
                            display_citation=citation["citation_text"],
                        )
                    )
                target.execute(
                    insert(generations).values(
                        id=generation_id,
                        status="staged",
                        profile_id=None,
                        readiness="partial_text_ready" if missing else "text_ready",
                        is_partial=bool(missing),
                        validation_json=json.dumps(
                            {
                                "missing_core_sources": missing,
                                "legacy_vectors": "excluded_unknown_preprocessing",
                            },
                            sort_keys=True,
                        ),
                        created_at=now,
                    )
                )
                for source in records["sources"]:
                    target.execute(
                        insert(generation_sources).values(
                            generation_id=generation_id,
                            source_version_id=source["version_id"],
                        )
                    )
                for chunk in records["chunks"]:
                    target.execute(
                        insert(generation_chunks).values(
                            generation_id=generation_id,
                            chunk_id=chunk["id"],
                            text_ready=True,
                            embedding_ready=False,
                        )
                    )
                CorpusService(storage)._build_fts(target, generation_id)
                target.execute(
                    corpus_state.update()
                    .where(corpus_state.c.id == 1)
                    .values(active_generation_id=generation_id)
                )
                target.execute(
                    generations.update()
                    .where(generations.c.id == generation_id)
                    .values(status="active", activated_at=now)
                )
            return CanonicalBundleService(storage).export(destination, generation_id)
        finally:
            storage.close()

    def _copy_artifact(self, source: dict, artifacts_root: Path) -> str:
        raw_uri = str(source["artifact_uri"])
        if raw_uri.startswith("s3://"):
            raise LegacyExportError(
                "This exporter currently requires legacy artifacts to be staged "
                "under --artifact-root; copy the selected S3 objects there first."
            )
        path = Path(raw_uri.removeprefix("file://"))
        if not path.is_absolute():
            path = self._artifact_root / path
        path = path.resolve()
        try:
            path.relative_to(self._artifact_root)
        except ValueError as exc:
            raise LegacyExportError(
                "A legacy artifact path escapes the selected artifact root."
            ) from exc
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise LegacyExportError(
                f"Legacy artifact is unavailable: {raw_uri}"
            ) from exc
        digest = hashlib.sha256(content).hexdigest()
        if digest != source["content_hash"]:
            raise LegacyExportError(
                f"Legacy artifact hash mismatch for source {source['slug']}."
            )
        destination = artifacts_root / "legacy" / f"{digest}.bin"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            destination.write_bytes(content)
        return destination.relative_to(artifacts_root.parent).as_posix()


def database_url_from_environment(name: str) -> str:
    if not name or not name.replace("_", "").isalnum():
        raise LegacyExportError("Legacy database environment name is invalid.")
    value = os.environ.get(name, "")
    if not value:
        raise LegacyExportError(f"Environment variable {name} is not set.")
    return value


def _mappings(connection, statement: str) -> list[dict]:
    return [dict(row) for row in connection.execute(text(statement)).mappings()]


def _selected_rows(connection, table: str, field: str, identifiers: list[str]):
    if not identifiers:
        return []
    # Table and field are internal constants; values remain bound parameters.
    statement = text(f"SELECT * FROM {table} WHERE {field} IN :identifiers").bindparams(
        identifiers=tuple(identifiers)
    )
    if connection.dialect.name != "postgresql":
        placeholders = ", ".join(
            f":identifier_{index}" for index in range(len(identifiers))
        )
        statement = text(
            f"SELECT * FROM {table} WHERE {field} IN ({placeholders})"
        )
        parameters = {
            f"identifier_{index}": value for index, value in enumerate(identifiers)
        }
        return [
            dict(row)
            for row in connection.execute(statement, parameters).mappings()
        ]
    statement = text(f"SELECT * FROM {table} WHERE {field} = ANY(:identifiers)")
    return [
        dict(row)
        for row in connection.execute(
            statement, {"identifiers": identifiers}
        ).mappings()
    ]


def _datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
