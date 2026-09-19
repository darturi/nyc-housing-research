from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, insert, select, update

from app.corpus.resource_parsers import ParsedResource, parse_resource
from app.corpus.service import CorpusService, CorpusValidationError, _write_artifact
from app.ingestion.legal_text import normalize_text
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    corpus_operations,
    corpus_state,
    documents,
    source_modules,
    source_versions,
)


@dataclass(frozen=True)
class ResourceMetadata:
    title: str
    publisher: str = ""
    category: str = "reference"
    jurisdiction: str = ""
    original_url: str | None = None
    model_use_allowed: bool = False


@dataclass(frozen=True)
class ResourceResult:
    status: str
    resource_id: str
    slug: str
    version_id: str | None
    generation_id: str | None
    chunk_count: int
    warnings: tuple[str, ...] = ()
    duplicate_of: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ResourceService:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage
        self._corpus = CorpusService(storage)

    def add(
        self,
        content: bytes,
        *,
        filename: str,
        metadata: ResourceMetadata,
        content_type: str | None = None,
        operation_id: str | None = None,
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        metadata = _validated_metadata(metadata)
        parsed = parse_resource(
            content,
            filename=filename,
            media_type=content_type,
            title=metadata.title,
        )
        content_hash = hashlib.sha256(content).hexdigest()
        duplicate = self._duplicate_resource(content_hash)
        if duplicate is not None:
            return ResourceResult(
                status="duplicate",
                resource_id=duplicate["id"],
                slug=duplicate["slug"],
                version_id=duplicate["version_id"],
                generation_id=self._active_generation_id(),
                chunk_count=duplicate["chunk_count"],
                duplicate_of=duplicate["id"],
            )
        resource_id = str(uuid.uuid4())
        slug = f"user-{resource_id}"
        return self._install_version(
            resource_id=resource_id,
            slug=slug,
            content=content,
            filename=filename,
            metadata=metadata,
            parsed=parsed,
            replace_existing=False,
            operation_id=operation_id,
        )

    def replace_file(
        self,
        resource_id: str,
        content: bytes,
        *,
        filename: str,
        metadata: ResourceMetadata | None = None,
        content_type: str | None = None,
        expected_version_id: str | None = None,
        operation_id: str | None = None,
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        current = self.get(resource_id)
        if expected_version_id and current["active_version_id"] != expected_version_id:
            raise CorpusValidationError(
                "The resource changed after it was opened; reload before replacing it."
            )
        selected_metadata = _validated_metadata(
            metadata or _metadata_from_resource(current)
        )
        parsed = parse_resource(
            content,
            filename=filename,
            media_type=content_type,
            title=selected_metadata.title,
        )
        return self._install_version(
            resource_id=resource_id,
            slug=current["slug"],
            content=content,
            filename=filename,
            metadata=selected_metadata,
            parsed=parsed,
            replace_existing=True,
            operation_id=operation_id,
        )

    def edit_metadata(
        self,
        resource_id: str,
        metadata: ResourceMetadata,
        *,
        expected_version_id: str | None = None,
        operation_id: str | None = None,
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        current = self.get(resource_id)
        if expected_version_id and current["active_version_id"] != expected_version_id:
            raise CorpusValidationError(
                "The resource changed after it was opened; reload before editing it."
            )
        artifact = self.original_path(resource_id, current["active_version_id"])
        provenance = current["provenance"]
        return self.replace_file(
            resource_id,
            artifact.read_bytes(),
            filename=str(provenance.get("original_basename") or "resource.txt"),
            metadata=metadata,
            content_type=str(provenance.get("media_type") or "text/plain"),
            expected_version_id=current["active_version_id"],
            operation_id=operation_id,
        )

    def remove(
        self, resource_id: str, *, operation_id: str | None = None
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        resource = self.get(resource_id)
        operation_id = operation_id or str(uuid.uuid4())
        with self._storage.corpus_engine.begin() as connection:
            receipt = _receipt(connection, operation_id)
            if receipt:
                return _result_from_receipt(receipt)
            selected = self._corpus._active_versions_by_slug(connection)
            selected.pop(resource["slug"], None)
            connection.execute(
                update(source_modules)
                .where(source_modules.c.id == resource_id)
                .values(enabled=False)
            )
            generation_id = self._corpus.publish_versions(
                connection, selected, allow_partial=True
            )
            result = ResourceResult(
                status="removed",
                resource_id=resource_id,
                slug=resource["slug"],
                version_id=resource["active_version_id"],
                generation_id=generation_id,
                chunk_count=resource["chunk_count"],
            )
            _store_receipt(connection, operation_id, "resource_remove", result)
            return result

    def restore(
        self,
        resource_id: str,
        *,
        version_id: str | None = None,
        operation_id: str | None = None,
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        resource = self.get(resource_id)
        selected_version = version_id or resource["latest_version_id"]
        self._assert_version(resource_id, selected_version)
        operation_id = operation_id or str(uuid.uuid4())
        with self._storage.corpus_engine.begin() as connection:
            receipt = _receipt(connection, operation_id)
            if receipt:
                return _result_from_receipt(receipt)
            selected = self._corpus._active_versions_by_slug(connection)
            selected[resource["slug"]] = selected_version
            connection.execute(
                update(source_modules)
                .where(source_modules.c.id == resource_id)
                .values(enabled=True)
            )
            generation_id = self._corpus.publish_versions(
                connection, selected, allow_partial=True
            )
            chunk_count = self._version_chunk_count(connection, selected_version)
            result = ResourceResult(
                status="restored",
                resource_id=resource_id,
                slug=resource["slug"],
                version_id=selected_version,
                generation_id=generation_id,
                chunk_count=chunk_count,
            )
            _store_receipt(connection, operation_id, "resource_restore", result)
            return result

    def set_model_use(
        self,
        resource_id: str,
        allowed: bool,
        *,
        operation_id: str | None = None,
    ) -> ResourceResult:
        existing = self._operation_result(operation_id)
        if existing is not None:
            return existing
        resource = self.get(resource_id)
        operation_id = operation_id or str(uuid.uuid4())
        with self._storage.corpus_engine.begin() as connection:
            receipt = _receipt(connection, operation_id)
            if receipt:
                return _result_from_receipt(receipt)
            connection.execute(
                update(source_modules)
                .where(source_modules.c.id == resource_id)
                .values(model_use_allowed=allowed)
            )
            selected = self._corpus._active_versions_by_slug(connection)
            generation_id = self._active_generation_id(connection)
            if resource["slug"] in selected:
                generation_id = self._corpus.publish_versions(
                    connection, selected, allow_partial=True
                )
            result = ResourceResult(
                status="model_use_updated",
                resource_id=resource_id,
                slug=resource["slug"],
                version_id=resource["active_version_id"]
                or resource["latest_version_id"],
                generation_id=generation_id,
                chunk_count=resource["chunk_count"],
            )
            _store_receipt(connection, operation_id, "resource_model_use", result)
            return result

    def list(self, *, include_removed: bool = True) -> list[dict[str, object]]:
        with self._storage.corpus_engine.connect() as connection:
            active_versions = self._corpus._active_versions_by_slug(connection)
            modules = list(
                connection.execute(
                    select(source_modules)
                    .where(source_modules.c.origin == "user")
                    .order_by(source_modules.c.name, source_modules.c.id)
                ).mappings()
            )
            result = []
            for module in modules:
                active_version = active_versions.get(module["slug"])
                if not include_removed and active_version is None:
                    continue
                latest = self._latest_version(connection, module["id"])
                selected_version = active_version or (latest["id"] if latest else None)
                selected = (
                    self._version(connection, selected_version)
                    if selected_version
                    else None
                )
                provenance = (
                    _json_object(selected["provenance_json"]) if selected else {}
                )
                result.append(
                    {
                        "id": module["id"],
                        "slug": module["slug"],
                        "name": provenance.get("title") or module["name"],
                        "origin": module["origin"],
                        "category": provenance.get("category", "reference"),
                        "publisher": provenance.get("publisher", ""),
                        "jurisdiction": provenance.get("jurisdiction", ""),
                        "original_url": provenance.get("original_url"),
                        "model_use_allowed": bool(module["model_use_allowed"]),
                        "active": active_version is not None,
                        "active_version_id": active_version,
                        "latest_version_id": latest["id"] if latest else None,
                        "added_at": (_iso(latest["retrieved_at"]) if latest else None),
                        "chunk_count": (
                            self._version_chunk_count(connection, selected_version)
                            if selected_version
                            else 0
                        ),
                        "warnings": provenance.get("warnings", []),
                        "media_type": provenance.get("media_type"),
                    }
                )
            return result

    def get(self, resource_id: str) -> dict[str, object]:
        with self._storage.corpus_engine.connect() as connection:
            module = (
                connection.execute(
                    select(source_modules).where(
                        source_modules.c.id == resource_id,
                        source_modules.c.origin == "user",
                    )
                )
                .mappings()
                .one_or_none()
            )
            if module is None:
                raise CorpusValidationError("User resource was not found.")
            active_versions = self._corpus._active_versions_by_slug(connection)
            active_version = active_versions.get(module["slug"])
            latest = self._latest_version(connection, resource_id)
            selected_id = active_version or (latest["id"] if latest else None)
            selected = self._version(connection, selected_id) if selected_id else None
            provenance = _json_object(selected["provenance_json"]) if selected else {}
            versions = list(
                connection.execute(
                    select(source_versions)
                    .where(source_versions.c.source_module_id == resource_id)
                    .order_by(
                        source_versions.c.retrieved_at.desc(),
                        source_versions.c.id.desc(),
                    )
                ).mappings()
            )
            return {
                "id": module["id"],
                "slug": module["slug"],
                "name": provenance.get("title") or module["name"],
                "origin": module["origin"],
                "model_use_allowed": bool(module["model_use_allowed"]),
                "active": active_version is not None,
                "active_version_id": active_version,
                "latest_version_id": latest["id"] if latest else None,
                "chunk_count": (
                    self._version_chunk_count(connection, selected_id)
                    if selected_id
                    else 0
                ),
                "provenance": provenance,
                "versions": [
                    {
                        "id": row["id"],
                        "content_hash": row["content_hash"],
                        "retrieved_at": _iso(row["retrieved_at"]),
                        "active": row["id"] == active_version,
                        "provenance": _json_object(row["provenance_json"]),
                        "chunk_count": self._version_chunk_count(connection, row["id"]),
                    }
                    for row in versions
                ],
            }

    def version(self, resource_id: str, version_id: str) -> dict[str, object]:
        self._assert_version(resource_id, version_id)
        with self._storage.corpus_engine.connect() as connection:
            row = self._version(connection, version_id)
        if row is None:
            raise CorpusValidationError("Resource version was not found.")
        return {
            "id": row["id"],
            "content_hash": row["content_hash"],
            "retrieved_at": _iso(row["retrieved_at"]),
            "provenance": _json_object(row["provenance_json"]),
        }

    def preview_chunks(
        self, resource_id: str, *, version_id: str | None = None, limit: int = 3
    ) -> list[dict[str, object]]:
        resource = self.get(resource_id)
        selected = (
            version_id or resource["active_version_id"] or resource["latest_version_id"]
        )
        if not selected:
            return []
        self._assert_version(resource_id, selected)
        with self._storage.corpus_engine.connect() as connection:
            rows = connection.execute(
                select(chunks.c.id, chunks.c.text, chunks.c.locator_json)
                .where(chunks.c.source_version_id == selected)
                .order_by(chunks.c.stable_id)
                .limit(max(1, min(limit, 20)))
            ).mappings()
            return [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "locator": _json_object(row["locator_json"]),
                }
                for row in rows
            ]

    def chunk(
        self, resource_id: str, version_id: str, chunk_id: str
    ) -> dict[str, object]:
        self._assert_version(resource_id, version_id)
        with self._storage.corpus_engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        chunks.c.id,
                        chunks.c.title,
                        chunks.c.text,
                        chunks.c.locator_json,
                    ).where(
                        chunks.c.id == chunk_id,
                        chunks.c.source_version_id == version_id,
                    )
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise CorpusValidationError("Resource excerpt was not found.")
        return {
            "id": row["id"],
            "title": row["title"],
            "text": row["text"],
            "locator": _json_object(row["locator_json"]),
        }

    def original_path(self, resource_id: str, version_id: str) -> Path:
        self._assert_version(resource_id, version_id)
        with self._storage.corpus_engine.connect() as connection:
            relative = connection.scalar(
                select(source_versions.c.artifact_uri).where(
                    source_versions.c.id == version_id
                )
            )
        path = (self._storage.paths.root / str(relative)).resolve()
        try:
            path.relative_to(self._storage.paths.artifacts.resolve())
        except ValueError as exc:
            raise CorpusValidationError("Resource artifact path is invalid.") from exc
        if not path.is_file():
            raise CorpusValidationError("Resource artifact is missing.")
        return path

    def _install_version(
        self,
        *,
        resource_id: str,
        slug: str,
        content: bytes,
        filename: str,
        metadata: ResourceMetadata,
        parsed: ParsedResource,
        replace_existing: bool,
        operation_id: str | None,
    ) -> ResourceResult:
        operation_id = operation_id or str(uuid.uuid4())
        content_hash = hashlib.sha256(content).hexdigest()
        metadata_payload = asdict(metadata)
        metadata_hash = hashlib.sha256(
            json.dumps(metadata_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        parser_version = f"user-resource-v1:{metadata_hash[:16]}"
        artifact_uri = _write_artifact(
            self._storage.paths.artifacts,
            slug,
            content_hash,
            content,
            parsed.media_type,
        )
        now = datetime.now(UTC)
        version_id = str(
            uuid.uuid5(uuid.UUID(resource_id), f"{content_hash}:{parser_version}")
        )
        try:
            with self._storage.corpus_engine.begin() as connection:
                receipt = _receipt(connection, operation_id)
                if receipt:
                    return _result_from_receipt(receipt)
                module = connection.scalar(
                    select(source_modules.c.id).where(
                        source_modules.c.id == resource_id
                    )
                )
                if replace_existing and module is None:
                    raise CorpusValidationError("User resource was not found.")
                if not replace_existing and module is not None:
                    raise CorpusValidationError(
                        "User resource identifier already exists."
                    )
                module_values = {
                    "slug": slug,
                    "name": metadata.title,
                    "source_type": "reference",
                    "publisher": metadata.publisher or "User-provided",
                    "jurisdiction": metadata.jurisdiction or "Not specified",
                    "source_url": metadata.original_url or "",
                    "scope_json": json.dumps(
                        {"description": metadata.category}, sort_keys=True
                    ),
                    "manifest_json": json.dumps(metadata_payload, sort_keys=True),
                    "origin": "user",
                    "acquisition_kind": "upload",
                    "model_use_allowed": metadata.model_use_allowed,
                    "enabled": True,
                }
                if module is None:
                    connection.execute(
                        insert(source_modules).values(id=resource_id, **module_values)
                    )
                else:
                    origin = connection.scalar(
                        select(source_modules.c.origin).where(
                            source_modules.c.id == resource_id
                        )
                    )
                    if origin != "user":
                        raise CorpusValidationError(
                            "Official sources cannot be replaced as user resources."
                        )
                    connection.execute(
                        update(source_modules)
                        .where(source_modules.c.id == resource_id)
                        .values(**module_values)
                    )
                existing_version = connection.scalar(
                    select(source_versions.c.id).where(
                        source_versions.c.id == version_id
                    )
                )
                if existing_version is None:
                    provenance = {
                        **metadata_payload,
                        "origin": "user",
                        "original_basename": _safe_basename(filename),
                        "media_type": parsed.media_type,
                        "size_bytes": len(content),
                        "imported_at": now.isoformat(),
                        "page_count": parsed.page_count,
                        "extracted_characters": parsed.extracted_characters,
                        "chunk_count": len(parsed.chunks),
                        "warnings": list(parsed.warnings),
                    }
                    connection.execute(
                        insert(source_versions).values(
                            id=version_id,
                            source_module_id=resource_id,
                            content_hash=content_hash,
                            parser_version=parser_version,
                            artifact_uri=artifact_uri,
                            retrieved_at=now,
                            last_checked_at=now,
                            effective_from=None,
                            effective_to=None,
                            validation_state=(
                                "validated_with_warnings"
                                if parsed.warnings
                                else "validated"
                            ),
                            validation_json=json.dumps(
                                {
                                    "documents": 1,
                                    "chunks": len(parsed.chunks),
                                    "warnings": list(parsed.warnings),
                                },
                                sort_keys=True,
                            ),
                            provenance_json=json.dumps(provenance, sort_keys=True),
                        )
                    )
                    document_id = str(uuid.uuid5(uuid.UUID(version_id), "document"))
                    connection.execute(
                        insert(documents).values(
                            id=document_id,
                            source_version_id=version_id,
                            stable_id="document",
                            title=metadata.title,
                            source_url=metadata.original_url or "",
                        )
                    )
                    connection.execute(
                        insert(chunks),
                        [
                            {
                                "id": hashlib.sha256(
                                    f"{version_id}:{item.stable_id}".encode()
                                ).hexdigest(),
                                "document_id": document_id,
                                "source_module_id": resource_id,
                                "source_version_id": version_id,
                                "stable_id": item.stable_id,
                                "citation": None,
                                "title": item.title,
                                "text": item.text,
                                "text_hash": hashlib.sha256(
                                    normalize_text(item.text).encode("utf-8")
                                ).hexdigest(),
                                "locator_json": json.dumps(
                                    item.locator, sort_keys=True
                                ),
                            }
                            for item in parsed.chunks
                        ],
                    )
                selected = self._corpus._active_versions_by_slug(connection)
                if selected.get(slug) == version_id:
                    generation_id = self._active_generation_id(connection)
                    status = "unchanged"
                else:
                    selected[slug] = version_id
                    generation_id = self._corpus.publish_versions(
                        connection, selected, allow_partial=True
                    )
                    status = "replaced" if replace_existing else "added"
                result = ResourceResult(
                    status=status,
                    resource_id=resource_id,
                    slug=slug,
                    version_id=version_id,
                    generation_id=generation_id,
                    chunk_count=len(parsed.chunks),
                    warnings=parsed.warnings,
                )
                _store_receipt(
                    connection,
                    operation_id,
                    "resource_replace" if replace_existing else "resource_add",
                    result,
                )
                return result
        except Exception:
            self._corpus._remove_unreferenced_artifacts([artifact_uri])
            raise

    def _duplicate_resource(self, content_hash: str) -> dict[str, object] | None:
        with self._storage.corpus_engine.connect() as connection:
            row = (
                connection.execute(
                    select(
                        source_modules.c.id,
                        source_modules.c.slug,
                        source_versions.c.id.label("version_id"),
                    )
                    .select_from(
                        source_modules.join(
                            source_versions,
                            source_versions.c.source_module_id == source_modules.c.id,
                        )
                    )
                    .where(
                        source_modules.c.origin == "user",
                        source_versions.c.content_hash == content_hash,
                    )
                    .order_by(source_versions.c.retrieved_at.desc())
                    .limit(1)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            return dict(row) | {
                "chunk_count": self._version_chunk_count(connection, row["version_id"])
            }

    def _latest_version(self, connection, resource_id: str):
        return (
            connection.execute(
                select(source_versions)
                .where(source_versions.c.source_module_id == resource_id)
                .order_by(
                    source_versions.c.retrieved_at.desc(), source_versions.c.id.desc()
                )
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )

    def _version(self, connection, version_id: str):
        return (
            connection.execute(
                select(source_versions).where(source_versions.c.id == version_id)
            )
            .mappings()
            .one_or_none()
        )

    def _assert_version(self, resource_id: str, version_id: str) -> None:
        with self._storage.corpus_engine.connect() as connection:
            exists = connection.scalar(
                select(source_versions.c.id).where(
                    source_versions.c.id == version_id,
                    source_versions.c.source_module_id == resource_id,
                )
            )
        if exists is None:
            raise CorpusValidationError("Resource version was not found.")

    def _version_chunk_count(self, connection, version_id: str) -> int:
        return int(
            connection.scalar(
                select(func.count())
                .select_from(chunks)
                .where(chunks.c.source_version_id == version_id)
            )
            or 0
        )

    def _active_generation_id(self, connection=None) -> str | None:
        if connection is not None:
            return connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
        with self._storage.corpus_engine.connect() as opened:
            return self._active_generation_id(opened)

    def _operation_result(self, operation_id: str | None) -> ResourceResult | None:
        if not operation_id:
            return None
        with self._storage.corpus_engine.connect() as connection:
            receipt = _receipt(connection, operation_id)
        return _result_from_receipt(receipt) if receipt else None


def _validated_metadata(metadata: ResourceMetadata) -> ResourceMetadata:
    title = " ".join(metadata.title.split())
    publisher = " ".join(metadata.publisher.split())
    category = " ".join(metadata.category.split()).lower() or "reference"
    jurisdiction = " ".join(metadata.jurisdiction.split())
    if not 1 <= len(title) <= 255:
        raise CorpusValidationError("Resource title must contain 1 to 255 characters.")
    if len(publisher) > 255 or len(jurisdiction) > 120 or len(category) > 80:
        raise CorpusValidationError("Resource metadata is too long.")
    original_url = metadata.original_url.strip() if metadata.original_url else None
    if original_url:
        parsed = urlparse(original_url)
        if parsed.scheme != "https" or not parsed.hostname or len(original_url) > 2_000:
            raise CorpusValidationError(
                "Original resource URL must be a valid HTTPS URL."
            )
    return replace(
        metadata,
        title=title,
        publisher=publisher,
        category=category,
        jurisdiction=jurisdiction,
        original_url=original_url,
    )


def _metadata_from_resource(resource: dict[str, object]) -> ResourceMetadata:
    provenance = resource["provenance"]
    if not isinstance(provenance, dict):
        provenance = {}
    return ResourceMetadata(
        title=str(provenance.get("title") or resource["name"]),
        publisher=str(provenance.get("publisher") or ""),
        category=str(provenance.get("category") or "reference"),
        jurisdiction=str(provenance.get("jurisdiction") or ""),
        original_url=(
            str(provenance["original_url"]) if provenance.get("original_url") else None
        ),
        model_use_allowed=bool(resource["model_use_allowed"]),
    )


def _receipt(connection, operation_id: str) -> dict[str, object] | None:
    raw = connection.scalar(
        select(corpus_operations.c.result_json).where(
            corpus_operations.c.operation_id == operation_id
        )
    )
    return _json_object(raw) if raw else None


def _store_receipt(
    connection, operation_id: str, operation_type: str, result: ResourceResult
) -> None:
    connection.execute(
        insert(corpus_operations).values(
            operation_id=operation_id,
            operation_type=operation_type,
            source_module_id=result.resource_id,
            source_version_id=result.version_id,
            generation_id=result.generation_id or "",
            result_json=json.dumps(result.as_dict(), sort_keys=True),
            created_at=datetime.now(UTC),
        )
    )


def _result_from_receipt(payload: dict[str, object]) -> ResourceResult:
    return ResourceResult(
        status=str(payload["status"]),
        resource_id=str(payload["resource_id"]),
        slug=str(payload["slug"]),
        version_id=(str(payload["version_id"]) if payload.get("version_id") else None),
        generation_id=(
            str(payload["generation_id"]) if payload.get("generation_id") else None
        ),
        chunk_count=int(payload["chunk_count"]),
        warnings=tuple(str(item) for item in payload.get("warnings", [])),
        duplicate_of=(
            str(payload["duplicate_of"]) if payload.get("duplicate_of") else None
        ),
    )


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str):
        return {}
    try:
        result = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return result if isinstance(result, dict) else {}


def _safe_basename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    if not name:
        return "resource"
    return name[:255]


def _iso(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
