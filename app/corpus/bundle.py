from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import delete, func, insert, select

from app.corpus.embeddings import decode_vector
from app.corpus.service import CorpusService, CorpusValidationError
from app.ingestion.legal_text import normalize_text
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    citations,
    corpus_state,
    documents,
    embedding_profiles,
    embeddings,
    generation_chunks,
    generation_sources,
    generations,
    source_modules,
    source_versions,
)

BUNDLE_FORMAT = "nyc-housing-corpus"
BUNDLE_VERSION = 1
MAX_BUNDLE_BYTES = 2 * 1024**3
MAX_MEMBER_BYTES = 300 * 1024**2


@dataclass(frozen=True)
class BundleSummary:
    generation_id: str
    source_count: int
    chunk_count: int
    embedding_count: int
    is_partial: bool


class CanonicalBundleService:
    """Export and import data-only corpus bundles.

    A bundle contains JSON records, original public-source artifacts, and numeric
    vector bytes. It never contains executable code, SQL, state.sqlite3, usage,
    sessions, questions, answers, logs, or credentials.
    """

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def export(
        self, destination: Path, generation_id: str | None = None
    ) -> BundleSummary:
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._storage.corpus_engine.connect() as connection:
            selected = generation_id or connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if selected is None:
                raise CorpusValidationError(
                    "No corpus generation is available to export."
                )
            payload, artifacts = _export_records(connection, selected, self._storage)

        records_bytes = _canonical_json(payload)
        members: dict[str, bytes] = {"records.json": records_bytes}
        members.update(artifacts)
        manifest = {
            "format": BUNDLE_FORMAT,
            "format_version": BUNDLE_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "generation_id": selected,
            "scope": "official_core_only",
            "excludes": [
                "application_state",
                "credentials",
                "hpd_bulk_rows",
                "logs",
                "questions_and_answers",
                "sessions",
                "user_resources",
                "usage_events",
            ],
            "members": {
                name: {"sha256": _sha256(content), "size_bytes": len(content)}
                for name, content in sorted(members.items())
            },
        }
        members["manifest.json"] = _canonical_json(manifest)
        _write_zip_atomic(destination, members)
        return _summary(payload)

    def inspect(self, bundle: Path) -> BundleSummary:
        _manifest, payload, _members = _read_and_validate_bundle(bundle)
        return _summary(payload)

    def import_bundle(
        self,
        bundle: Path,
        *,
        activate: bool = True,
        allow_partial: bool = False,
    ) -> BundleSummary:
        _manifest, payload, members = _read_and_validate_bundle(bundle)
        summary = _summary(payload)
        if summary.is_partial and not allow_partial:
            raise CorpusValidationError(
                "Partial bundle import requires explicit --allow-partial approval."
            )
        _validate_records(payload, members)

        artifact_uris = _publish_artifacts(payload, members, self._storage)
        with self._storage.corpus_engine.begin() as connection:
            active_user_versions = _active_user_versions(connection)
            _insert_records(connection, payload, artifact_uris)
            CorpusService(self._storage)._build_fts(connection, summary.generation_id)
            if activate:
                if active_user_versions:
                    selected = active_user_versions | _payload_versions_by_slug(payload)
                    merged_id = CorpusService(self._storage).publish_versions(
                        connection,
                        selected,
                        allow_partial=allow_partial,
                        activate=True,
                    )
                    merged = connection.execute(
                        select(generations).where(generations.c.id == merged_id)
                    ).mappings().one()
                    summary = BundleSummary(
                        generation_id=merged_id,
                        source_count=len(selected),
                        chunk_count=int(
                            connection.scalar(
                                select(func.count())
                                .select_from(generation_chunks)
                                .where(
                                    generation_chunks.c.generation_id == merged_id
                                )
                            )
                            or 0
                        ),
                        embedding_count=summary.embedding_count,
                        is_partial=bool(merged["is_partial"]),
                    )
                else:
                    CorpusService(self._storage)._activate_in_transaction(
                        connection, summary.generation_id, datetime.now(UTC)
                    )
        return summary


def _export_records(connection, generation_id: str, storage: LocalStorage):
    generation = (
        connection.execute(select(generations).where(generations.c.id == generation_id))
        .mappings()
        .one_or_none()
    )
    if generation is None:
        raise CorpusValidationError(f"Unknown generation: {generation_id}")
    version_ids = list(
        connection.scalars(
            select(generation_sources.c.source_version_id)
            .select_from(
                generation_sources.join(
                    source_versions,
                    source_versions.c.id == generation_sources.c.source_version_id,
                ).join(
                    source_modules,
                    source_modules.c.id == source_versions.c.source_module_id,
                )
            )
            .where(
                generation_sources.c.generation_id == generation_id,
                source_modules.c.origin == "core",
            )
        )
    )
    if not version_ids:
        raise CorpusValidationError(
            "The selected generation has no official core sources to export."
        )
    chunk_ids = list(
        connection.scalars(
            select(generation_chunks.c.chunk_id)
            .select_from(
                generation_chunks.join(
                    chunks, chunks.c.id == generation_chunks.c.chunk_id
                )
            )
            .where(
                generation_chunks.c.generation_id == generation_id,
                chunks.c.source_version_id.in_(version_ids),
            )
        )
    )
    document_ids = list(
        connection.scalars(
            select(chunks.c.document_id).where(chunks.c.id.in_(chunk_ids))
        )
    )
    module_ids = list(
        connection.scalars(
            select(source_versions.c.source_module_id).where(
                source_versions.c.id.in_(version_ids)
            )
        )
    )
    embedding_rows = _rows(
        connection,
        select(embeddings).where(embeddings.c.chunk_id.in_(chunk_ids)),
    )
    profile_ids = sorted({row["profile_id"] for row in embedding_rows})
    artifacts: dict[str, bytes] = {}
    version_rows = _rows(
        connection, select(source_versions).where(source_versions.c.id.in_(version_ids))
    )
    for row in version_rows:
        source = (storage.paths.root / row["artifact_uri"]).resolve()
        try:
            source.relative_to(storage.paths.artifacts.resolve())
        except ValueError as exc:
            raise CorpusValidationError(
                "Corpus artifact path escapes its workspace."
            ) from exc
        content = source.read_bytes()
        if _sha256(content) != row["content_hash"]:
            raise CorpusValidationError(
                f"Artifact hash mismatch for version {row['id']}."
            )
        artifacts[f"artifacts/{row['content_hash']}.bin"] = content
        row["bundle_artifact"] = f"artifacts/{row['content_hash']}.bin"

    encoded_embeddings = []
    for row in embedding_rows:
        decode_vector(
            row["vector_bytes"],
            dimension=row["dimension"],
            dtype=row["dtype"],
            checksum=row["checksum"],
        )
        row["vector_base64"] = base64.b64encode(row.pop("vector_bytes")).decode("ascii")
        encoded_embeddings.append(row)

    payload = {
        "format_version": BUNDLE_VERSION,
        "generation": _jsonable(dict(generation)),
        "source_modules": _jsonable(
            _rows(
                connection,
                select(source_modules).where(source_modules.c.id.in_(module_ids)),
            )
        ),
        "source_versions": _jsonable(version_rows),
        "documents": _jsonable(
            _rows(connection, select(documents).where(documents.c.id.in_(document_ids)))
        ),
        "chunks": _jsonable(
            _rows(connection, select(chunks).where(chunks.c.id.in_(chunk_ids)))
        ),
        "citations": _jsonable(
            _rows(
                connection, select(citations).where(citations.c.chunk_id.in_(chunk_ids))
            )
        ),
        "embedding_profiles": _jsonable(
            _rows(
                connection,
                select(embedding_profiles).where(
                    embedding_profiles.c.id.in_(profile_ids)
                ),
            )
        ),
        "embeddings": _jsonable(encoded_embeddings),
        "generation_sources": _jsonable(
            _rows(
                connection,
                select(generation_sources).where(
                    generation_sources.c.generation_id == generation_id,
                    generation_sources.c.source_version_id.in_(version_ids),
                ),
            )
        ),
        "generation_chunks": _jsonable(
            _rows(
                connection,
                select(generation_chunks).where(
                    generation_chunks.c.generation_id == generation_id,
                    generation_chunks.c.chunk_id.in_(chunk_ids),
                ),
            )
        ),
    }
    return payload, artifacts


def _read_and_validate_bundle(bundle: Path):
    bundle = bundle.expanduser().resolve()
    if not bundle.is_file():
        raise CorpusValidationError(f"Bundle does not exist: {bundle}")
    if bundle.stat().st_size > MAX_BUNDLE_BYTES:
        raise CorpusValidationError("Bundle exceeds the supported size limit.")
    members: dict[str, bytes] = {}
    total = 0
    try:
        with zipfile.ZipFile(bundle) as archive:
            for info in archive.infolist():
                _validate_member(info)
                total += info.file_size
                if total > MAX_BUNDLE_BYTES:
                    raise CorpusValidationError(
                        "Expanded bundle exceeds the size limit."
                    )
                members[info.filename] = archive.read(info)
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise CorpusValidationError("Bundle is not a valid portable archive.") from exc
    if set(members) < {"manifest.json", "records.json"}:
        raise CorpusValidationError("Bundle is missing its manifest or records.")
    try:
        manifest = json.loads(members["manifest.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusValidationError("Bundle manifest JSON is invalid.") from exc
    if manifest.get("format") != BUNDLE_FORMAT or manifest.get("format_version") != 1:
        raise CorpusValidationError("Unsupported corpus bundle format.")
    declared = manifest.get("members")
    if not isinstance(declared, dict) or set(declared) != set(members) - {
        "manifest.json"
    }:
        raise CorpusValidationError("Bundle member manifest is incomplete.")
    for name, metadata in declared.items():
        content = members[name]
        if metadata.get("size_bytes") != len(content) or metadata.get(
            "sha256"
        ) != _sha256(content):
            raise CorpusValidationError(f"Bundle member verification failed: {name}")
    try:
        payload = json.loads(members["records.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorpusValidationError("Bundle record JSON is invalid.") from exc
    return manifest, payload, members


def _validate_member(info: zipfile.ZipInfo) -> None:
    path = PurePosixPath(info.filename)
    if path.is_absolute() or ".." in path.parts or "\\" in info.filename:
        raise CorpusValidationError("Bundle contains an unsafe path.")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode) or info.is_dir():
        raise CorpusValidationError("Bundle links and directories are not supported.")
    if info.file_size > MAX_MEMBER_BYTES:
        raise CorpusValidationError(f"Bundle member is too large: {info.filename}")
    allowed = info.filename in {"manifest.json", "records.json"} or (
        len(path.parts) == 2 and path.parts[0] == "artifacts" and path.suffix == ".bin"
    )
    if not allowed:
        raise CorpusValidationError(f"Unsupported bundle member: {info.filename}")


def _validate_records(payload: dict, members: dict[str, bytes]) -> None:
    if payload.get("format_version") != BUNDLE_VERSION:
        raise CorpusValidationError("Unsupported bundle record version.")
    required = {
        "generation",
        "source_modules",
        "source_versions",
        "documents",
        "chunks",
        "citations",
        "embedding_profiles",
        "embeddings",
        "generation_sources",
        "generation_chunks",
    }
    if not required <= set(payload):
        raise CorpusValidationError("Bundle records are incomplete.")
    ids = {
        "modules": {row["id"] for row in payload["source_modules"]},
        "versions": {row["id"] for row in payload["source_versions"]},
        "documents": {row["id"] for row in payload["documents"]},
        "chunks": {row["id"] for row in payload["chunks"]},
        "profiles": {row["id"] for row in payload["embedding_profiles"]},
    }
    if any(row.get("origin", "core") != "core" for row in payload["source_modules"]):
        raise CorpusValidationError(
            "Portable corpus bundles cannot contain user-provided resources."
        )
    generation_id = payload["generation"]["id"]
    for row in payload["source_versions"]:
        if row["source_module_id"] not in ids["modules"]:
            raise CorpusValidationError("Bundle source-version reference is invalid.")
        member = row.get("bundle_artifact")
        if member not in members or _sha256(members[member]) != row["content_hash"]:
            raise CorpusValidationError("Bundle source artifact is invalid.")
    for row in payload["documents"]:
        if row["source_version_id"] not in ids["versions"]:
            raise CorpusValidationError("Bundle document reference is invalid.")
    for row in payload["chunks"]:
        if (
            row["document_id"] not in ids["documents"]
            or row["source_version_id"] not in ids["versions"]
            or row["source_module_id"] not in ids["modules"]
            or _sha256(normalize_text(row["text"]).encode("utf-8")) != row["text_hash"]
        ):
            raise CorpusValidationError("Bundle chunk reference or hash is invalid.")
    for row in payload["citations"]:
        if row["chunk_id"] not in ids["chunks"]:
            raise CorpusValidationError("Bundle citation reference is invalid.")
    for row in payload["embeddings"]:
        if (
            row["chunk_id"] not in ids["chunks"]
            or row["profile_id"] not in ids["profiles"]
        ):
            raise CorpusValidationError("Bundle embedding reference is invalid.")
        try:
            raw = base64.b64decode(row["vector_base64"], validate=True)
        except ValueError as exc:
            raise CorpusValidationError(
                "Bundle embedding encoding is invalid."
            ) from exc
        decode_vector(
            raw,
            dimension=row["dimension"],
            dtype=row["dtype"],
            checksum=row["checksum"],
        )
    if any(
        row["generation_id"] != generation_id
        for row in payload["generation_sources"] + payload["generation_chunks"]
    ):
        raise CorpusValidationError("Bundle mixes corpus generations.")


def _publish_artifacts(payload: dict, members: dict[str, bytes], storage: LocalStorage):
    result = {}
    for row in payload["source_versions"]:
        content = members[row["bundle_artifact"]]
        directory = storage.paths.artifacts / "bundle" / row["content_hash"][:2]
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{row['content_hash']}.bin"
        if not destination.exists():
            _write_bytes_atomic(destination, content)
        result[row["id"]] = destination.relative_to(storage.paths.root).as_posix()
    return result


def _active_user_versions(connection) -> dict[str, str]:
    active = connection.scalar(
        select(corpus_state.c.active_generation_id).where(corpus_state.c.id == 1)
    )
    if active is None:
        return {}
    rows = connection.execute(
        select(source_modules.c.slug, generation_sources.c.source_version_id)
        .select_from(
            generation_sources.join(
                source_versions,
                source_versions.c.id == generation_sources.c.source_version_id,
            ).join(
                source_modules,
                source_modules.c.id == source_versions.c.source_module_id,
            )
        )
        .where(
            generation_sources.c.generation_id == active,
            source_modules.c.origin == "user",
        )
    )
    return {slug: version_id for slug, version_id in rows}


def _payload_versions_by_slug(payload: dict) -> dict[str, str]:
    module_slugs = {row["id"]: row["slug"] for row in payload["source_modules"]}
    versions = {
        row["id"]: module_slugs[row["source_module_id"]]
        for row in payload["source_versions"]
    }
    return {
        versions[row["source_version_id"]]: row["source_version_id"]
        for row in payload["generation_sources"]
    }


def _insert_records(connection, payload: dict, artifact_uris: dict[str, str]) -> None:
    generation_id = payload["generation"]["id"]
    if connection.scalar(
        select(generations.c.id).where(generations.c.id == generation_id)
    ):
        raise CorpusValidationError(f"Generation {generation_id} is already installed.")
    tables_and_rows = (
        (source_modules, payload["source_modules"]),
        (source_versions, payload["source_versions"]),
        (documents, payload["documents"]),
        (chunks, payload["chunks"]),
        (citations, payload["citations"]),
        (embedding_profiles, payload["embedding_profiles"]),
    )
    for table, rows in tables_and_rows:
        for original in rows:
            row = dict(original)
            row.pop("bundle_artifact", None)
            if table is source_versions:
                row["artifact_uri"] = artifact_uris[row["id"]]
            _parse_datetimes(table, row)
            existing = (
                connection.execute(select(table).where(table.c.id == row["id"]))
                .mappings()
                .one_or_none()
            )
            if existing is None:
                connection.execute(insert(table).values(**row))
            elif any(
                existing[key] != value
                for key, value in row.items()
                if key != "last_checked_at"
            ):
                raise CorpusValidationError(
                    f"Bundle record conflicts with local {table.name}: {row['id']}"
                )
    generation = dict(payload["generation"])
    generation["status"] = "staged"
    generation["activated_at"] = None
    _parse_datetimes(generations, generation)
    connection.execute(insert(generations).values(**generation))
    for row in payload["generation_sources"]:
        connection.execute(insert(generation_sources).values(**row))
    for row in payload["generation_chunks"]:
        connection.execute(insert(generation_chunks).values(**row))
    for original in payload["embeddings"]:
        row = dict(original)
        row["vector_bytes"] = base64.b64decode(row.pop("vector_base64"), validate=True)
        _parse_datetimes(embeddings, row)
        connection.execute(insert(embeddings).values(**row))
    connection.execute(delete(corpus_state).where(corpus_state.c.id != 1))


def _parse_datetimes(table, row: dict) -> None:
    for column in table.columns:
        if (
            column.name in row
            and isinstance(row[column.name], str)
            and column.type.python_type is datetime
        ):
            row[column.name] = datetime.fromisoformat(row[column.name])


def _summary(payload: dict) -> BundleSummary:
    return BundleSummary(
        generation_id=payload["generation"]["id"],
        source_count=len(payload["generation_sources"]),
        chunk_count=len(payload["generation_chunks"]),
        embedding_count=len(payload["embeddings"]),
        is_partial=bool(payload["generation"]["is_partial"]),
    )


def _rows(connection, statement) -> list[dict]:
    return [dict(row) for row in connection.execute(statement).mappings()]


def _jsonable(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_zip_atomic(destination: Path, members: dict[str, bytes]) -> None:
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".bundle-", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for member, content in sorted(members.items()):
                archive.writestr(member, content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_atomic(destination: Path, content: bytes) -> None:
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".artifact-", suffix=".tmp"
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
