from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, insert, select, text, update

from app.corpus.manifests import (
    SourceManifest,
    load_all_manifests,
    load_core_manifests,
)
from app.ingestion.amlegal_xml import (
    hmc_xml_from_zip,
    parse_hmc_xml_sections,
    parse_nyc_admin_xml_range_sections,
)
from app.ingestion.citations import (
    CitationCandidate,
    detect_citation_type,
    extract_citations,
    normalize_citation,
)
from app.ingestion.hpd_guidance import (
    canonical_guidance_url,
    pages_from_bundle,
    parse_hpd_guidance_page,
)
from app.ingestion.legal_text import (
    ParsedSection,
    artifact_bytes_to_text,
    normalize_text,
    split_sections,
)
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    citations,
    corpus_state,
    documents,
    embeddings,
    generation_chunks,
    generation_sources,
    generations,
    source_modules,
    source_versions,
)


class CorpusValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceArtifact:
    slug: str
    content: bytes
    source_url: str
    content_type: str | None
    retrieved_at: datetime
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class CanonicalDocument:
    stable_id: str
    title: str
    source_url: str
    sections: tuple[ParsedSection, ...]


@dataclass(frozen=True)
class ParsedSource:
    manifest: SourceManifest
    artifact: SourceArtifact
    content_hash: str
    semantic_hash: str
    documents: tuple[CanonicalDocument, ...]
    citations: frozenset[str]


@dataclass(frozen=True)
class CorpusStatus:
    active_generation_id: str | None
    readiness: str
    is_partial: bool
    source_count: int
    chunk_count: int
    embedding_ready_count: int


class CorpusService:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage
        self._manifests = load_all_manifests()
        self._core_slugs = frozenset(load_core_manifests())

    @property
    def manifests(self) -> dict[str, SourceManifest]:
        return dict(self._manifests)

    def install_artifacts(
        self,
        artifacts: list[SourceArtifact],
        *,
        activate: bool = True,
        allow_partial: bool = False,
    ) -> str:
        if not artifacts:
            raise CorpusValidationError("No source artifacts were supplied.")
        seen: set[str] = set()
        parsed: list[ParsedSource] = []
        for artifact in artifacts:
            if artifact.slug in seen:
                raise CorpusValidationError(
                    f"Duplicate source artifact: {artifact.slug}"
                )
            seen.add(artifact.slug)
            manifest = self._manifests.get(artifact.slug)
            if manifest is None:
                raise CorpusValidationError(f"Unknown source: {artifact.slug}")
            parsed_source = _parse_source(manifest, artifact)
            _validate_source(parsed_source)
            parsed.append(parsed_source)

        artifact_uris = {
            source.manifest.slug: _write_artifact(
                self._storage.paths.artifacts,
                source.manifest.slug,
                source.content_hash,
                source.artifact.content,
                source.artifact.content_type,
            )
            for source in parsed
        }

        now = _utc_now()
        selected_generation: str | None = None
        try:
            with self._storage.corpus_engine.begin() as connection:
                active_generation = connection.scalar(
                    select(corpus_state.c.active_generation_id).where(
                        corpus_state.c.id == 1
                    )
                )
                previous_versions = self._active_versions_by_slug(connection)
                selected_versions = dict(previous_versions)
                for source in parsed:
                    version_id = self._store_source(
                        connection,
                        source,
                        artifact_uris[source.manifest.slug],
                    )
                    selected_versions[source.manifest.slug] = version_id

                if active_generation and selected_versions == previous_versions:
                    selected_generation = active_generation
                else:
                    selected_generation = self._publish_generation(
                        connection,
                        selected_versions,
                        allow_partial=allow_partial,
                        activate=activate,
                        now=now,
                    )
        finally:
            self._remove_unreferenced_artifacts(artifact_uris.values())
        if selected_generation is None:
            raise CorpusValidationError("Corpus generation could not be published.")
        return selected_generation

    def activate(self, generation_id: str, *, allow_partial: bool = False) -> None:
        with self._storage.corpus_engine.begin() as connection:
            row = (
                connection.execute(
                    select(generations).where(generations.c.id == generation_id)
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise CorpusValidationError(f"Unknown generation: {generation_id}")
            if row["readiness"] not in {
                "text_ready",
                "partial_text_ready",
                "hybrid_ready",
                "hybrid_partial",
            }:
                raise CorpusValidationError("Generation is not validated for search.")
            if row["is_partial"] and not allow_partial:
                raise CorpusValidationError(
                    "Partial generation activation requires explicit approval."
                )
            self._activate_in_transaction(connection, generation_id, _utc_now())

    def check_artifacts(
        self, artifacts: list[SourceArtifact]
    ) -> list[dict[str, object]]:
        """Validate downloaded artifacts and report changes without publishing."""
        if not artifacts:
            raise CorpusValidationError("No source artifacts were supplied.")
        seen: set[str] = set()
        parsed_sources: list[ParsedSource] = []
        for artifact in artifacts:
            if artifact.slug in seen:
                raise CorpusValidationError(
                    f"Duplicate source artifact: {artifact.slug}"
                )
            seen.add(artifact.slug)
            manifest = self._manifests.get(artifact.slug)
            if manifest is None:
                raise CorpusValidationError(f"Unknown source: {artifact.slug}")
            parsed = _parse_source(manifest, artifact)
            _validate_source(parsed)
            parsed_sources.append(parsed)

        results: list[dict[str, object]] = []
        with self._storage.corpus_engine.begin() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            for parsed in parsed_sources:
                active_version = None
                if active is not None:
                    active_version = (
                        connection.execute(
                            select(
                                source_versions.c.id,
                                source_versions.c.content_hash,
                            )
                            .select_from(
                                generation_sources.join(
                                    source_versions,
                                    source_versions.c.id
                                    == generation_sources.c.source_version_id,
                                ).join(
                                    source_modules,
                                    source_modules.c.id
                                    == source_versions.c.source_module_id,
                                )
                            )
                            .where(
                                generation_sources.c.generation_id == active,
                                source_modules.c.slug == parsed.manifest.slug,
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                state = "not_installed"
                if active_version is not None:
                    state = (
                        "current"
                        if active_version["content_hash"] == parsed.content_hash
                        else "update_available"
                    )
                    connection.execute(
                        update(source_versions)
                        .where(source_versions.c.id == active_version["id"])
                        .values(last_checked_at=parsed.artifact.retrieved_at)
                    )
                results.append(
                    {
                        "slug": parsed.manifest.slug,
                        "state": state,
                        "active_content_hash": (
                            active_version["content_hash"]
                            if active_version is not None
                            else None
                        ),
                        "checked_content_hash": parsed.content_hash,
                        "checked_at": parsed.artifact.retrieved_at.isoformat(),
                    }
                )
        return results

    def rollback(self) -> str:
        with self._storage.corpus_engine.begin() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            previous = connection.scalar(
                select(generations.c.id)
                .where(
                    generations.c.id != active,
                    generations.c.status == "retained",
                )
                .order_by(generations.c.activated_at.desc())
                .limit(1)
            )
            if previous is None:
                raise CorpusValidationError("No retained generation is available.")
            self._activate_in_transaction(connection, previous, _utc_now())
            return previous

    def status(self) -> CorpusStatus:
        with self._storage.corpus_engine.connect() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if active is None:
                return CorpusStatus(None, "not_installed", False, 0, 0, 0)
            generation = (
                connection.execute(
                    select(generations).where(generations.c.id == active)
                )
                .mappings()
                .one()
            )
            source_count = connection.scalar(
                select(text("count(*)"))
                .select_from(generation_sources)
                .where(generation_sources.c.generation_id == active)
            )
            chunk_count = connection.scalar(
                select(text("count(*)"))
                .select_from(generation_chunks)
                .where(generation_chunks.c.generation_id == active)
            )
            embedding_ready = connection.scalar(
                select(text("count(*)"))
                .select_from(generation_chunks)
                .where(
                    generation_chunks.c.generation_id == active,
                    generation_chunks.c.embedding_ready.is_(True),
                )
            )
            return CorpusStatus(
                active_generation_id=active,
                readiness=generation["readiness"],
                is_partial=generation["is_partial"],
                source_count=int(source_count or 0),
                chunk_count=int(chunk_count or 0),
                embedding_ready_count=int(embedding_ready or 0),
            )

    def verify(self, generation_id: str | None = None) -> dict[str, object]:
        with self._storage.corpus_engine.connect() as connection:
            selected = generation_id or connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if selected is None:
                raise CorpusValidationError("No generation is available to verify.")
            generation = (
                connection.execute(
                    select(generations).where(generations.c.id == selected)
                )
                .mappings()
                .one_or_none()
            )
            if generation is None:
                raise CorpusValidationError("Generation pointer is invalid.")
            rows = connection.execute(
                select(
                    source_modules.c.slug,
                    source_versions.c.content_hash,
                    source_versions.c.artifact_uri,
                )
                .select_from(
                    generation_sources.join(
                        source_versions,
                        source_versions.c.id == generation_sources.c.source_version_id,
                    ).join(
                        source_modules,
                        source_modules.c.id == source_versions.c.source_module_id,
                    )
                )
                .where(generation_sources.c.generation_id == selected)
            ).mappings()
            verified = []
            for row in rows:
                artifact_path = (
                    self._storage.paths.root / row["artifact_uri"]
                ).resolve()
                try:
                    artifact_path.relative_to(self._storage.paths.artifacts.resolve())
                except ValueError as exc:
                    raise CorpusValidationError(
                        f"Artifact path escapes storage for {row['slug']}."
                    ) from exc
                if not artifact_path.is_file():
                    raise CorpusValidationError(
                        f"Missing artifact for {row['slug']}: {row['artifact_uri']}"
                    )
                content_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
                if content_hash != row["content_hash"]:
                    raise CorpusValidationError(
                        f"Artifact hash mismatch for {row['slug']}."
                    )
                verified.append(row["slug"])
            chunk_count = int(
                connection.scalar(
                    select(func.count())
                    .select_from(generation_chunks)
                    .where(generation_chunks.c.generation_id == selected)
                )
                or 0
            )
            fts_count = int(
                connection.scalar(
                    text(
                        "SELECT count(*) FROM chunk_fts f JOIN generation_chunks gc "
                        "ON gc.chunk_id = f.chunk_id WHERE gc.generation_id = :id "
                        "AND f.generation_id = '__shared__'"
                    ),
                    {"id": selected},
                )
                or 0
            )
            if fts_count != chunk_count:
                raise CorpusValidationError(
                    "Generation FTS row count does not match its chunk membership."
                )
            law_without_citation = connection.scalar(
                select(chunks.c.id)
                .select_from(
                    generation_chunks.join(
                        chunks, chunks.c.id == generation_chunks.c.chunk_id
                    ).join(
                        source_modules,
                        source_modules.c.id == chunks.c.source_module_id,
                    )
                )
                .where(
                    generation_chunks.c.generation_id == selected,
                    source_modules.c.source_type == "law",
                    chunks.c.citation.is_(None),
                )
                .limit(1)
            )
            if law_without_citation:
                raise CorpusValidationError(
                    "A law chunk has no exact display citation."
                )
            embedding_ready_count = int(
                connection.scalar(
                    select(func.count())
                    .select_from(generation_chunks)
                    .where(
                        generation_chunks.c.generation_id == selected,
                        generation_chunks.c.embedding_ready.is_(True),
                    )
                )
                or 0
            )
            if embedding_ready_count:
                matched_embeddings = int(
                    connection.scalar(
                        select(func.count())
                        .select_from(
                            generation_chunks.join(
                                embeddings,
                                embeddings.c.chunk_id == generation_chunks.c.chunk_id,
                            )
                        )
                        .where(
                            generation_chunks.c.generation_id == selected,
                            generation_chunks.c.embedding_ready.is_(True),
                            embeddings.c.profile_id == generation["profile_id"],
                        )
                    )
                    or 0
                )
                if matched_embeddings != embedding_ready_count:
                    raise CorpusValidationError(
                        "Embedding-ready membership does not match the "
                        "generation profile."
                    )
            return {
                "generation_id": selected,
                "status": generation["status"],
                "readiness": generation["readiness"],
                "profile_id": generation["profile_id"],
                "verified_sources": sorted(verified),
                "chunk_count": chunk_count,
                "fts_count": fts_count,
                "embedding_ready_count": embedding_ready_count,
                "citation_invariants": "verified",
            }

    def verify_all_retained(self) -> dict[str, object]:
        """Verify every active/retained generation and all of its references."""
        with self._storage.corpus_engine.connect() as connection:
            generation_ids = list(
                connection.scalars(
                    select(generations.c.id)
                    .where(generations.c.status.in_(["active", "retained"]))
                    .order_by(generations.c.created_at)
                )
            )
        if not generation_ids:
            raise CorpusValidationError(
                "No retained generation is available to verify."
            )
        results = [self.verify(generation_id) for generation_id in generation_ids]
        return {
            "status": "verified",
            "generation_count": len(results),
            "generations": results,
            "verified_source_references": sum(
                len(result["verified_sources"]) for result in results
            ),
            "citation_invariants": "verified",
        }

    def source_statuses(self) -> list[dict[str, object]]:
        now = _utc_now()
        with self._storage.corpus_engine.connect() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            result = []
            for slug, manifest in sorted(self._manifests.items()):
                row = None
                if active:
                    row = (
                        connection.execute(
                            select(
                                source_versions.c.id,
                                source_versions.c.content_hash,
                                source_versions.c.retrieved_at,
                                source_versions.c.last_checked_at,
                                source_versions.c.validation_state,
                            )
                            .select_from(
                                generation_sources.join(
                                    source_versions,
                                    source_versions.c.id
                                    == generation_sources.c.source_version_id,
                                ).join(
                                    source_modules,
                                    source_modules.c.id
                                    == source_versions.c.source_module_id,
                                )
                            )
                            .where(
                                generation_sources.c.generation_id == active,
                                source_modules.c.slug == slug,
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                checked = _aware(row["last_checked_at"]) if row else None
                retained_version_id = connection.scalar(
                    select(source_versions.c.id)
                    .select_from(
                        source_versions.join(
                            source_modules,
                            source_modules.c.id == source_versions.c.source_module_id,
                        )
                    )
                    .where(source_modules.c.slug == slug)
                    .order_by(source_versions.c.retrieved_at.desc())
                    .limit(1)
                )
                result.append(
                    {
                        "slug": slug,
                        "name": manifest.name,
                        "source_type": manifest.source_type,
                        "publisher": manifest.publisher,
                        "scope": manifest.scope,
                        "source_url": manifest.source_url,
                        "license_status": manifest.license_status,
                        "redistribution_allowed": manifest.redistribution_allowed,
                        "role": manifest.role,
                        "pack_id": manifest.pack_id,
                        "authority_category": manifest.authority_category,
                        "installed": row is not None,
                        "retained_version_available": retained_version_id is not None,
                        "active_version_id": row["id"] if row else None,
                        "content_hash": row["content_hash"] if row else None,
                        "retrieved_at": (
                            _aware(row["retrieved_at"]).isoformat() if row else None
                        ),
                        "last_checked_at": checked.isoformat() if checked else None,
                        "check_overdue": (
                            checked is None or now - checked > timedelta(days=30)
                        ),
                        "validation_state": (
                            row["validation_state"] if row else "not_installed"
                        ),
                    }
                )
        return result

    def remove_sources(self, slugs: list[str]) -> str:
        selected = self._validated_optional_slugs(slugs)
        with self._storage.corpus_engine.begin() as connection:
            versions = self._active_versions_by_slug(connection)
            installed = sorted(set(selected) & set(versions))
            if not installed:
                raise CorpusValidationError(
                    "None of the selected sources is installed."
                )
            for slug in installed:
                versions.pop(slug)
            return self._publish_generation(
                connection,
                versions,
                allow_partial=True,
                activate=True,
                now=_utc_now(),
            )

    def restore_sources(self, slugs: list[str]) -> str:
        selected = self._validated_optional_slugs(slugs)
        with self._storage.corpus_engine.begin() as connection:
            versions = self._active_versions_by_slug(connection)
            restored: list[str] = []
            for slug in selected:
                module_id = connection.scalar(
                    select(source_modules.c.id).where(source_modules.c.slug == slug)
                )
                if module_id is None:
                    continue
                version_id = connection.scalar(
                    select(source_versions.c.id)
                    .where(source_versions.c.source_module_id == module_id)
                    .order_by(source_versions.c.retrieved_at.desc())
                    .limit(1)
                )
                if version_id is not None:
                    versions[slug] = version_id
                    restored.append(slug)
            if not restored:
                raise CorpusValidationError(
                    "No retained version is available for the selected sources."
                )
            return self._publish_generation(
                connection,
                versions,
                allow_partial=True,
                activate=True,
                now=_utc_now(),
            )

    def _validated_optional_slugs(self, slugs: list[str]) -> list[str]:
        selected = list(dict.fromkeys(slugs))
        if not selected:
            raise CorpusValidationError("Select at least one optional source.")
        unknown = sorted(set(selected) - set(self._manifests))
        if unknown:
            raise CorpusValidationError("Unknown source(s): " + ", ".join(unknown))
        core = sorted(set(selected) & self._core_slugs)
        if core:
            raise CorpusValidationError(
                "Core sources cannot be removed or restored as pack modules: "
                + ", ".join(core)
            )
        return selected

    def _active_versions_by_slug(self, connection) -> dict[str, str]:
        active = connection.scalar(
            select(corpus_state.c.active_generation_id).where(corpus_state.c.id == 1)
        )
        if active is None:
            return {}
        rows = connection.execute(
            select(source_modules.c.slug, source_versions.c.id)
            .select_from(
                generation_sources.join(
                    source_versions,
                    source_versions.c.id == generation_sources.c.source_version_id,
                ).join(
                    source_modules,
                    source_modules.c.id == source_versions.c.source_module_id,
                )
            )
            .where(generation_sources.c.generation_id == active)
        )
        return {slug: version_id for slug, version_id in rows}

    def publish_versions(
        self,
        connection,
        selected_versions: dict[str, str],
        *,
        allow_partial: bool = True,
        activate: bool = True,
        now: datetime | None = None,
    ) -> str:
        """Publish a generation from an explicit source-to-version mapping."""
        return self._publish_generation(
            connection,
            selected_versions,
            allow_partial=allow_partial,
            activate=activate,
            now=now or _utc_now(),
        )

    def _publish_generation(
        self,
        connection,
        selected_versions: dict[str, str],
        *,
        allow_partial: bool,
        activate: bool,
        now: datetime,
    ) -> str:
        missing = sorted(self._core_slugs - set(selected_versions))
        if missing and not allow_partial:
            raise CorpusValidationError(
                "Core generation is missing source(s): " + ", ".join(missing)
            )
        version_ids = list(dict.fromkeys(selected_versions.values()))
        if version_ids:
            installed = set(
                connection.scalars(
                    select(source_versions.c.id).where(
                        source_versions.c.id.in_(version_ids)
                    )
                )
            )
            unknown = sorted(set(version_ids) - installed)
            if unknown:
                raise CorpusValidationError(
                    "Unknown source version(s): " + ", ".join(unknown)
                )
        active = connection.scalar(
            select(corpus_state.c.active_generation_id).where(corpus_state.c.id == 1)
        )
        profile_id = (
            connection.scalar(
                select(generations.c.profile_id).where(generations.c.id == active)
            )
            if active
            else None
        )
        chunk_rows = []
        if version_ids:
            chunk_rows = list(
                connection.execute(
                    select(
                        chunks.c.id,
                        source_modules.c.model_use_allowed,
                    )
                    .select_from(
                        chunks.join(
                            source_modules,
                            source_modules.c.id == chunks.c.source_module_id,
                        )
                    )
                    .where(chunks.c.source_version_id.in_(version_ids))
                    .order_by(chunks.c.id)
                ).mappings()
            )
        ready_ids: set[str] = set()
        if profile_id and chunk_rows:
            ready_ids = set(
                connection.scalars(
                    select(embeddings.c.chunk_id).where(
                        embeddings.c.profile_id == profile_id,
                        embeddings.c.chunk_id.in_([row["id"] for row in chunk_rows]),
                    )
                )
            )
        eligible_ids = {
            row["id"] for row in chunk_rows if bool(row["model_use_allowed"])
        }
        embedding_ready_ids = ready_ids & eligible_ids
        if profile_id and embedding_ready_ids:
            readiness = (
                "hybrid_ready"
                if embedding_ready_ids == eligible_ids
                else "hybrid_partial"
            )
        else:
            readiness = "partial_text_ready" if missing else "text_ready"
        generation_id = str(uuid.uuid4())
        validation = {
            "missing_sources": missing,
            "embedding_eligible_chunks": len(eligible_ids),
            "embedding_ready_chunks": len(embedding_ready_ids),
        }
        connection.execute(
            insert(generations).values(
                id=generation_id,
                status="staged",
                profile_id=profile_id,
                readiness=readiness,
                is_partial=bool(missing),
                validation_json=json.dumps(validation, sort_keys=True),
                created_at=now,
                activated_at=None,
            )
        )
        if version_ids:
            connection.execute(
                insert(generation_sources),
                [
                    {
                        "generation_id": generation_id,
                        "source_version_id": version_id,
                    }
                    for version_id in version_ids
                ],
            )
        if chunk_rows:
            connection.execute(
                insert(generation_chunks),
                [
                    {
                        "generation_id": generation_id,
                        "chunk_id": row["id"],
                        "text_ready": True,
                        "embedding_ready": row["id"] in embedding_ready_ids,
                    }
                    for row in chunk_rows
                ],
            )
        self._build_fts(connection, generation_id)
        if activate:
            self._activate_in_transaction(connection, generation_id, now)
        return generation_id

    def _store_source(self, connection, parsed: ParsedSource, artifact_uri: str) -> str:
        manifest = parsed.manifest
        module_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"nyc-housing:{manifest.slug}"))
        existing_module = connection.scalar(
            select(source_modules.c.id).where(source_modules.c.slug == manifest.slug)
        )
        values = {
            "slug": manifest.slug,
            "name": manifest.name,
            "source_type": manifest.source_type,
            "publisher": manifest.publisher,
            "jurisdiction": manifest.jurisdiction,
            "source_url": manifest.source_url,
            "scope_json": json.dumps({"description": manifest.scope}),
            "manifest_json": json.dumps(manifest.raw, sort_keys=True),
            "origin": "core",
            "acquisition_kind": "managed_download",
            "model_use_allowed": True,
            "enabled": True,
        }
        if existing_module is None:
            connection.execute(insert(source_modules).values(id=module_id, **values))
        else:
            module_id = existing_module
            connection.execute(
                update(source_modules)
                .where(source_modules.c.id == module_id)
                .values(**values)
            )

        version_id = str(
            uuid.uuid5(
                uuid.UUID(module_id),
                f"{parsed.content_hash}:{manifest.parser_version}",
            )
        )
        existing_version = connection.scalar(
            select(source_versions.c.id).where(source_versions.c.id == version_id)
        )
        if existing_version is not None:
            connection.execute(
                update(source_versions)
                .where(source_versions.c.id == version_id)
                .values(
                    last_checked_at=parsed.artifact.retrieved_at,
                    validation_json=_source_validation_json(parsed),
                )
            )
            return version_id

        prior_versions = connection.execute(
            select(source_versions.c.id, source_versions.c.validation_json).where(
                source_versions.c.source_module_id == module_id,
                source_versions.c.parser_version == manifest.parser_version,
            )
        ).mappings()
        for prior in prior_versions:
            try:
                validation = json.loads(prior["validation_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if validation.get("semantic_hash") == parsed.semantic_hash:
                connection.execute(
                    update(source_versions)
                    .where(source_versions.c.id == prior["id"])
                    .values(
                        last_checked_at=parsed.artifact.retrieved_at,
                        validation_json=_source_validation_json(parsed),
                    )
                )
                return prior["id"]

        connection.execute(
            insert(source_versions).values(
                id=version_id,
                source_module_id=module_id,
                content_hash=parsed.content_hash,
                parser_version=manifest.parser_version,
                artifact_uri=artifact_uri,
                retrieved_at=parsed.artifact.retrieved_at,
                last_checked_at=parsed.artifact.retrieved_at,
                effective_from=None,
                effective_to=None,
                validation_state="validated",
                validation_json=_source_validation_json(parsed),
                provenance_json=json.dumps(
                    {
                        "origin": "core",
                        "role": manifest.role,
                        "pack_id": manifest.pack_id,
                        "authority_category": manifest.authority_category,
                        "source_url": manifest.source_url,
                        "publisher": manifest.publisher,
                    },
                    sort_keys=True,
                ),
            )
        )
        for document in parsed.documents:
            document_id = str(uuid.uuid5(uuid.UUID(version_id), document.stable_id))
            connection.execute(
                insert(documents).values(
                    id=document_id,
                    source_version_id=version_id,
                    stable_id=document.stable_id,
                    title=document.title,
                    source_url=document.source_url,
                )
            )
            for section in document.sections:
                text_hash = hashlib.sha256(
                    normalize_text(section.text).encode("utf-8")
                ).hexdigest()
                chunk_id = hashlib.sha256(
                    f"{version_id}:{document.stable_id}:{section.section_key}".encode()
                ).hexdigest()
                connection.execute(
                    insert(chunks).values(
                        id=chunk_id,
                        document_id=document_id,
                        source_module_id=module_id,
                        source_version_id=version_id,
                        stable_id=section.section_key,
                        citation=section.citation,
                        title=section.title,
                        text=section.text,
                        text_hash=text_hash,
                        locator_json=json.dumps(
                            {"section": section.citation or section.section_key},
                            sort_keys=True,
                        ),
                    )
                )
                candidates = extract_citations(section.text)
                if section.citation:
                    normalized = normalize_citation(section.citation)
                    candidates.append(
                        CitationCandidate(
                            citation_text=section.citation,
                            normalized_citation=normalized,
                            citation_type=detect_citation_type(normalized),
                        )
                    )
                seen: set[str] = set()
                for candidate in candidates:
                    if candidate.normalized_citation in seen:
                        continue
                    seen.add(candidate.normalized_citation)
                    citation_id = hashlib.sha256(
                        f"{chunk_id}:{candidate.normalized_citation}".encode()
                    ).hexdigest()
                    connection.execute(
                        insert(citations).values(
                            id=citation_id,
                            chunk_id=chunk_id,
                            normalized_citation=candidate.normalized_citation,
                            display_citation=candidate.citation_text,
                        )
                    )
        return version_id

    def _remove_unreferenced_artifacts(self, artifact_uris) -> None:
        root = self._storage.paths.artifacts.resolve()
        with self._storage.corpus_engine.connect() as connection:
            for relative in set(artifact_uris):
                referenced = connection.scalar(
                    select(func.count())
                    .select_from(source_versions)
                    .where(source_versions.c.artifact_uri == relative)
                )
                if referenced:
                    continue
                path = (self._storage.paths.root / relative).resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                path.unlink(missing_ok=True)

    def _build_fts(self, connection, generation_id: str) -> None:
        # Corpus schema 3 indexes each immutable chunk once. Generation
        # membership is joined at query time, including historical searches.
        connection.execute(
            text("DELETE FROM chunk_fts WHERE generation_id <> '__shared__'")
        )
        connection.execute(
            text("""
            INSERT INTO chunk_fts (chunk_id, generation_id, title, citation, body)
            SELECT c.id, '__shared__', coalesce(c.title, ''),
                   coalesce(c.citation, ''), c.text
            FROM chunks c
            WHERE c.id IN (SELECT chunk_id FROM generation_chunks)
              AND c.id NOT IN (SELECT chunk_id FROM chunk_fts)
        """)
        )

    def _activate_in_transaction(
        self, connection, generation_id: str, now: datetime
    ) -> None:
        old_active = connection.scalar(
            select(corpus_state.c.active_generation_id).where(corpus_state.c.id == 1)
        )
        if old_active and old_active != generation_id:
            connection.execute(
                update(generations)
                .where(generations.c.id == old_active)
                .values(status="retained")
            )
        connection.execute(
            update(generations)
            .where(generations.c.id == generation_id)
            .values(status="active", activated_at=now)
        )
        connection.execute(
            update(corpus_state)
            .where(corpus_state.c.id == 1)
            .values(active_generation_id=generation_id)
        )


def _parse_source(manifest: SourceManifest, artifact: SourceArtifact) -> ParsedSource:
    if manifest.parser == "hmc_xml_zip":
        xml_content = hmc_xml_from_zip(artifact.content)
        parsed_documents = (
            CanonicalDocument(
                stable_id=manifest.slug,
                title=manifest.name,
                source_url=artifact.source_url,
                sections=tuple(parse_hmc_xml_sections(xml_content)),
            ),
        )
    elif manifest.parser == "nyc_admin_xml_range":
        section_start = str(manifest.raw.get("section_start", ""))
        section_end = str(manifest.raw.get("section_end", ""))
        if not section_start or not section_end:
            raise CorpusValidationError(
                f"Administrative Code range is missing for {manifest.slug}."
            )
        parsed_documents = (
            CanonicalDocument(
                stable_id=manifest.slug,
                title=manifest.name,
                source_url=manifest.source_url,
                sections=tuple(
                    parse_nyc_admin_xml_range_sections(
                        artifact.content,
                        section_start=section_start,
                        section_end=section_end,
                    )
                ),
            ),
        )
    elif manifest.parser in {"state_law_pdf", "good_cause_pdf"}:
        raw_text = artifact_bytes_to_text(
            artifact.content, artifact.content_type, artifact.source_url
        )
        sections = split_sections(raw_text, manifest.slug)
        if manifest.parser == "good_cause_pdf":
            allowed = {f"Real Property Law § {number}" for number in range(210, 217)}
            allowed.add("Real Property Law § 231-C")
            sections = [section for section in sections if section.citation in allowed]
        parsed_documents = (
            CanonicalDocument(
                stable_id=manifest.slug,
                title=manifest.name,
                source_url=artifact.source_url,
                sections=tuple(sections),
            ),
        )
    elif manifest.parser in {"hpd_guidance_bundle", "curated_guidance_bundle"}:
        parsed_documents = tuple(
            CanonicalDocument(
                stable_id=hashlib.sha256(page.url.encode()).hexdigest()[:24],
                title=page.title,
                source_url=(
                    canonical_guidance_url(page.url)
                    if manifest.parser == "hpd_guidance_bundle"
                    else _canonical_https_url(page.url)
                ),
                sections=tuple(
                    parse_hpd_guidance_page(page.url, page.title, page.html)
                ),
            )
            for page in pages_from_bundle(artifact.content)
        )
    else:
        raise CorpusValidationError(f"Unsupported parser: {manifest.parser}")
    normalized_citations = frozenset(
        normalize_citation(section.citation)
        for document in parsed_documents
        for section in document.sections
        if section.citation
    )
    return ParsedSource(
        manifest=manifest,
        artifact=artifact,
        content_hash=hashlib.sha256(artifact.content).hexdigest(),
        semantic_hash=_semantic_hash(parsed_documents),
        documents=parsed_documents,
        citations=normalized_citations,
    )


def _source_validation_json(parsed: ParsedSource) -> str:
    return json.dumps(
        {
            "documents": len(parsed.documents),
            "citations": len(parsed.citations),
            "semantic_hash": parsed.semantic_hash,
            "http": {
                "etag": parsed.artifact.etag,
                "last_modified": parsed.artifact.last_modified,
                "content_type": parsed.artifact.content_type,
                "source_url": parsed.artifact.source_url,
            },
        },
        sort_keys=True,
    )


def _semantic_hash(documents: tuple[CanonicalDocument, ...]) -> str:
    payload = [
        {
            "stable_id": document.stable_id,
            "title": document.title,
            "sections": [
                {
                    "section_key": section.section_key,
                    "citation": section.citation,
                    "title": section.title,
                    "text": normalize_text(section.text),
                }
                for section in document.sections
            ],
        }
        for document in documents
    ]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_source(source: ParsedSource) -> None:
    if len(source.documents) < source.manifest.minimum_documents:
        raise CorpusValidationError(
            f"{source.manifest.slug} produced too few documents: "
            f"{len(source.documents)}."
        )
    sections = [
        section for document in source.documents for section in document.sections
    ]
    if not sections or any(not section.text.strip() for section in sections):
        raise CorpusValidationError(
            f"{source.manifest.slug} produced empty legal content."
        )
    if source.manifest.source_type == "law" and any(
        section.citation is None for section in sections
    ):
        raise CorpusValidationError(
            f"{source.manifest.slug} produced a law section without a citation."
        )
    missing = set(source.manifest.required_citations) - set(source.citations)
    if missing:
        raise CorpusValidationError(
            f"{source.manifest.slug} is missing required citation(s): "
            + ", ".join(sorted(missing))
        )


def _write_artifact(
    artifacts_root: Path,
    slug: str,
    content_hash: str,
    content: bytes,
    content_type: str | None,
) -> str:
    extension = _artifact_extension(content_type, content)
    target_directory = artifacts_root / "sources" / slug
    target_directory.mkdir(parents=True, exist_ok=True)
    target = target_directory / f"{content_hash}.{extension}"
    if not target.exists():
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target_directory, prefix=f".{content_hash}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.relative_to(artifacts_root.parent).as_posix()


def _artifact_extension(content_type: str | None, content: bytes) -> str:
    normalized = (content_type or "").lower()
    if content.startswith(b"PK\x03\x04"):
        return "zip"
    if "pdf" in normalized or content.startswith(b"%PDF"):
        return "pdf"
    if "json" in normalized:
        return "json"
    if "html" in normalized:
        return "html"
    if "markdown" in normalized:
        return "md"
    if normalized.startswith("text/"):
        return "txt"
    return "bin"


def _canonical_https_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CorpusValidationError("Guidance document URL must use HTTPS.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)
