from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np
from sqlalchemy import and_, case, select, text

from app.corpus.embeddings import EmbeddingValidationError, normalize_query_vector
from app.ingestion.citations import normalize_citation
from app.retrieval.query_quality import expand_query_text, focused_query_texts
from app.storage.database import LocalStorage
from app.storage.schema import (
    chunks,
    citations,
    corpus_state,
    documents,
    embedding_profiles,
    embeddings,
    generation_chunks,
    generations,
    source_modules,
    source_versions,
)

TOKEN_PATTERN = re.compile(r"[\w§-]+", re.UNICODE)
VECTOR_CACHE_MAX_BYTES = 256 * 1024**2
SOURCE_HINTS: tuple[tuple[re.Pattern, str], ...] = (
    (
        re.compile(
            r"\bgood\s+cause\b|article\s+6-a|"
            r"\bRPL\s*(?:§|section)?\s*(?:21[0-6]|231-c)",
            re.I,
        ),
        "ny-real-property-law-good-cause",
    ),
    (
        re.compile(
            r"\bHPD\b.*\b(complaint|inspection|certif|dismiss|clear)\b|"
            r"\b(complaint|ecertification|clear\s+violations)\b",
            re.I,
        ),
        "hpd-guidance",
    ),
    (
        re.compile(
            r"\bRPAPL\b|real\s+property\s+actions\s+and\s+proceedings|"
            r"\bnonpayment\b|\bholdover\b|summary\s+proceeding|"
            r"rent\s+default|section\s+seven\s+eleven",
            re.I,
        ),
        "ny-rpapl",
    ),
    (
        re.compile(r"\bMDL\b|multiple\s+dwelling(?:\s+law)?", re.I),
        "ny-multiple-dwelling-law",
    ),
    (
        re.compile(
            r"\bHMC\b|housing\s+(?:maintenance\s+)?code|"
            r"(?:NYC\s+)?admin(?:istrative)?\s+code|\b27-\d{3,5}\b|"
            r"\bheat\s+season\b|minimum\s+(?:indoor\s+)?temperature|"
            r"fit\s+for\s+(?:human\s+)?habitation|good\s+repair|"
            r"dut(?:y|ies)\s+of\s+(?:an?\s+)?owner",
            re.I,
        ),
        "nyc-housing-maintenance-code",
    ),
)


@dataclass(frozen=True)
class LocalSearchFilters:
    source_slug: str | None = None
    source_type: str | None = None
    jurisdiction: str | None = None


@dataclass(frozen=True)
class LocalSearchResult:
    chunk_id: str
    source_slug: str
    source_name: str
    source_type: str
    jurisdiction: str
    source_url: str
    publisher: str
    retrieved_at: str
    last_checked_at: str
    effective_from: str | None
    effective_to: str | None
    citation: str | None
    title: str | None
    text: str
    score: float
    match_types: tuple[str, ...]


@dataclass(frozen=True)
class LocalSearchResponse:
    generation_id: str
    method: str
    semantic_status: str
    results: tuple[LocalSearchResult, ...]


@dataclass(frozen=True)
class _VectorIndex:
    rows: tuple[dict, ...]
    matrix: np.ndarray
    size_bytes: int


class _VectorIndexCache:
    def __init__(self, max_bytes: int = VECTOR_CACHE_MAX_BYTES) -> None:
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple[object, str, str], _VectorIndex] = (
            OrderedDict()
        )
        self._size_bytes = 0
        self._lock = threading.Lock()

    def get(self, key, loader) -> _VectorIndex:
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached
        loaded = loader()
        if loaded.size_bytes > self._max_bytes:
            return loaded
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                self._entries.move_to_end(key)
                return existing
            self._entries[key] = loaded
            self._size_bytes += loaded.size_bytes
            while self._size_bytes > self._max_bytes and len(self._entries) > 1:
                _old_key, old = self._entries.popitem(last=False)
                self._size_bytes -= old.size_bytes
        return loaded


_VECTOR_INDEX_CACHE = _VectorIndexCache()


class LocalSearch:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def search(
        self,
        query: str,
        *,
        filters: LocalSearchFilters | None = None,
        limit: int = 10,
        query_vector: list[float] | None = None,
        embedding_profile_id: str | None = None,
    ) -> LocalSearchResponse:
        query = " ".join(query.split())
        if not query:
            raise ValueError("Search query cannot be empty.")
        if not 1 <= limit <= 100:
            raise ValueError("Search limit must be between 1 and 100.")
        filters = filters or LocalSearchFilters()
        with self._storage.corpus_engine.connect() as connection:
            generation_id = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            if generation_id is None:
                raise ValueError("No active legal corpus is installed.")
            exact = self._exact(connection, generation_id, query, filters, limit)
            candidate_limit = limit * 4
            keyword_queries = list(
                dict.fromkeys(
                    [query, expand_query_text(query), *focused_query_texts(query)]
                )
            )
            keyword_sets = [
                self._keyword(
                    connection,
                    generation_id,
                    keyword_query,
                    filters,
                    candidate_limit,
                )
                for keyword_query in keyword_queries
            ]
            hinted_filters = _source_hint_filters(query, filters)
            if hinted_filters is not None:
                keyword_sets.extend(
                    self._keyword(
                        connection,
                        generation_id,
                        keyword_query,
                        hinted_filters,
                        candidate_limit,
                    )
                    for keyword_query in keyword_queries
                )
            keyword = _fuse_ranked_sets(keyword_sets, candidate_limit)
            semantic: list[tuple[dict, float]] = []
            semantic_status = "not_requested"
            if query_vector is not None or embedding_profile_id is not None:
                if query_vector is None or embedding_profile_id is None:
                    raise ValueError(
                        "Semantic search requires both a query vector and profile ID."
                    )
                semantic = self._semantic(
                    connection,
                    generation_id,
                    filters,
                    query_vector,
                    embedding_profile_id,
                    limit * 4,
                )
                semantic_status = "available" if semantic else "index_unavailable"
            merged = _merge_ranked(exact, keyword, semantic, limit)
            methods = ["exact", "keyword"]
            if semantic_status == "available":
                methods.append("vector")
            return LocalSearchResponse(
                generation_id=generation_id,
                method="+".join(methods),
                semantic_status=semantic_status,
                results=tuple(merged),
            )

    def _exact(self, connection, generation_id, query, filters, limit):
        normalized = normalize_citation(query)
        conditions = [
            citations.c.normalized_citation == normalized,
            generation_chunks.c.generation_id == generation_id,
            *_filter_conditions(filters),
        ]
        statement = (
            _result_select()
            .select_from(_result_join(citations_table=True))
            .where(and_(*conditions))
            .order_by(
                case((chunks.c.citation == normalized, 0), else_=1),
                chunks.c.id,
            )
            .limit(limit)
        )
        return [(dict(row), 1.0) for row in connection.execute(statement).mappings()]

    def _keyword(self, connection, generation_id, query, filters, limit):
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        conditions, parameters = _sql_filter_conditions(filters)
        parameters.update(
            {"generation_id": generation_id, "query": fts_query, "limit": limit}
        )
        where_clause = " AND ".join(conditions)
        rows = connection.execute(
            text(
                f"""
                SELECT
                    c.id AS chunk_id,
                    sm.slug AS source_slug,
                    sm.name AS source_name,
                    sm.source_type AS source_type,
                    sm.jurisdiction AS jurisdiction,
                    d.source_url AS source_url,
                    sm.publisher AS publisher,
                    sv.retrieved_at AS retrieved_at,
                    sv.last_checked_at AS last_checked_at,
                    sv.effective_from AS effective_from,
                    sv.effective_to AS effective_to,
                    c.citation AS citation,
                    c.title AS title,
                    c.text AS text,
                    bm25(chunk_fts, 0.0, 0.0, 10.0, 8.0, 1.0) AS rank
                FROM chunk_fts
                JOIN chunks c ON c.id = chunk_fts.chunk_id
                JOIN source_modules sm ON sm.id = c.source_module_id
                JOIN source_versions sv ON sv.id = c.source_version_id
                JOIN documents d ON d.id = c.document_id
                WHERE chunk_fts MATCH :query
                  AND chunk_fts.generation_id = :generation_id
                  AND {where_clause}
                ORDER BY rank ASC, c.id ASC
                LIMIT :limit
                """
            ),
            parameters,
        ).mappings()
        return [(dict(row), -float(row["rank"])) for row in rows]

    def _semantic(
        self,
        connection,
        generation_id,
        filters,
        query_vector,
        profile_id,
        limit,
    ):
        generation_profile = connection.scalar(
            select(generations.c.profile_id).where(generations.c.id == generation_id)
        )
        if generation_profile != profile_id:
            return []
        profile = (
            connection.execute(
                select(embedding_profiles).where(embedding_profiles.c.id == profile_id)
            )
            .mappings()
            .one_or_none()
        )
        if profile is None:
            return []
        normalized_query = normalize_query_vector(query_vector, profile["dimension"])
        cache_key = (id(self._storage.corpus_engine), generation_id, profile_id)
        index = _VECTOR_INDEX_CACHE.get(
            cache_key,
            lambda: self._load_vector_index(
                connection, generation_id, profile_id, profile["dimension"]
            ),
        )
        eligible = [
            index_number
            for index_number, row in enumerate(index.rows)
            if _row_matches_filters(row, filters)
        ]
        if not eligible:
            return []
        query_array = np.asarray(normalized_query, dtype=np.float32)
        if len(eligible) == len(index.rows):
            scores = index.matrix @ query_array
            candidate_indices = np.arange(len(index.rows))
        else:
            candidate_indices = np.asarray(eligible, dtype=np.int64)
            scores = index.matrix[candidate_indices] @ query_array
        keep = min(limit, len(candidate_indices))
        if keep < len(candidate_indices):
            local_top = np.argpartition(scores, -keep)[-keep:]
            candidate_indices = candidate_indices[local_top]
            scores = scores[local_top]
        ranked = [
            (index.rows[int(row_index)], float(score))
            for row_index, score in zip(candidate_indices, scores, strict=True)
            if math.isfinite(float(score))
        ]
        ranked.sort(key=lambda item: (-item[1], item[0]["chunk_id"]))
        return ranked[:limit]

    def _load_vector_index(
        self, connection, generation_id: str, profile_id: str, dimension: int
    ) -> _VectorIndex:
        statement = (
            _result_select(
                embeddings.c.vector_bytes,
                embeddings.c.dimension,
                embeddings.c.dtype,
                embeddings.c.normalized,
                embeddings.c.checksum,
            )
            .select_from(_result_join(embedding_table=True))
            .where(
                generation_chunks.c.generation_id == generation_id,
                embeddings.c.profile_id == profile_id,
            )
            .order_by(chunks.c.id)
        )
        rows = []
        vectors = []
        for row in connection.execute(statement).mappings():
            raw = row["vector_bytes"]
            if not row["normalized"]:
                raise EmbeddingValidationError("Embedding is not normalized.")
            if row["dtype"] != "float32-le":
                raise EmbeddingValidationError(
                    f"Unsupported vector dtype: {row['dtype']}"
                )
            if row["dimension"] != dimension or len(raw) != dimension * 4:
                raise EmbeddingValidationError(
                    "Embedding byte length does not match its profile dimension."
                )
            if hashlib.sha256(raw).hexdigest() != row["checksum"]:
                raise EmbeddingValidationError("Embedding checksum mismatch.")
            vector = np.frombuffer(raw, dtype="<f4")
            if not np.isfinite(vector).all():
                raise EmbeddingValidationError("Embedding contains non-finite values.")
            vectors.append(vector)
            result_row = dict(row)
            for field in (
                "vector_bytes",
                "dimension",
                "dtype",
                "normalized",
                "checksum",
            ):
                result_row.pop(field)
            rows.append(result_row)
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.size == 0:
            matrix = np.empty((0, dimension), dtype=np.float32)
        matrix.setflags(write=False)
        metadata_bytes = sum(
            len(str(value).encode("utf-8"))
            for row in rows
            for value in row.values()
            if not isinstance(value, bytes)
        )
        return _VectorIndex(tuple(rows), matrix, matrix.nbytes + metadata_bytes)


def _result_join(*, citations_table: bool = False, embedding_table: bool = False):
    joined = generation_chunks.join(chunks, chunks.c.id == generation_chunks.c.chunk_id)
    joined = joined.join(
        source_modules, source_modules.c.id == chunks.c.source_module_id
    )
    joined = joined.join(
        source_versions, source_versions.c.id == chunks.c.source_version_id
    )
    joined = joined.join(documents, documents.c.id == chunks.c.document_id)
    if citations_table:
        joined = joined.join(citations, citations.c.chunk_id == chunks.c.id)
    if embedding_table:
        joined = joined.join(embeddings, embeddings.c.chunk_id == chunks.c.id)
    return joined


def _result_select(*additional):
    return select(
        chunks.c.id.label("chunk_id"),
        source_modules.c.slug.label("source_slug"),
        source_modules.c.name.label("source_name"),
        source_modules.c.source_type.label("source_type"),
        source_modules.c.jurisdiction.label("jurisdiction"),
        documents.c.source_url.label("source_url"),
        source_modules.c.publisher.label("publisher"),
        source_versions.c.retrieved_at.label("retrieved_at"),
        source_versions.c.last_checked_at.label("last_checked_at"),
        source_versions.c.effective_from.label("effective_from"),
        source_versions.c.effective_to.label("effective_to"),
        chunks.c.citation,
        chunks.c.title,
        chunks.c.text,
        *additional,
    )


def _filter_conditions(filters: LocalSearchFilters):
    conditions = []
    if filters.source_slug:
        conditions.append(source_modules.c.slug == filters.source_slug)
    if filters.source_type:
        conditions.append(source_modules.c.source_type == filters.source_type)
    if filters.jurisdiction:
        conditions.append(source_modules.c.jurisdiction == filters.jurisdiction)
    return conditions


def _sql_filter_conditions(
    filters: LocalSearchFilters,
) -> tuple[list[str], dict[str, object]]:
    conditions = ["1 = 1"]
    parameters: dict[str, object] = {}
    for field, value in (
        ("slug", filters.source_slug),
        ("source_type", filters.source_type),
        ("jurisdiction", filters.jurisdiction),
    ):
        if value:
            parameter = f"filter_{field}"
            conditions.append(f"sm.{field} = :{parameter}")
            parameters[parameter] = value
    return conditions, parameters


def _row_matches_filters(row: dict, filters: LocalSearchFilters) -> bool:
    return (
        (not filters.source_slug or row["source_slug"] == filters.source_slug)
        and (not filters.source_type or row["source_type"] == filters.source_type)
        and (not filters.jurisdiction or row["jurisdiction"] == filters.jurisdiction)
    )


def _source_hint_filters(
    query: str, filters: LocalSearchFilters
) -> LocalSearchFilters | None:
    if filters.source_slug:
        return None
    for pattern, source_slug in SOURCE_HINTS:
        if pattern.search(query):
            return LocalSearchFilters(
                source_slug=source_slug,
                source_type=filters.source_type,
                jurisdiction=filters.jurisdiction,
            )
    return None


def _fuse_ranked_sets(result_sets, limit: int):
    rows: dict[str, dict] = {}
    scores: dict[str, float] = {}
    for result_set in result_sets:
        for rank, (row, _raw_score) in enumerate(result_set, start=1):
            chunk_id = row["chunk_id"]
            rows.setdefault(chunk_id, row)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
    ordered = sorted(rows, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [(rows[chunk_id], scores[chunk_id]) for chunk_id in ordered[:limit]]


def _fts_query(query: str) -> str:
    terms = [term for term in TOKEN_PATTERN.findall(query.lower()) if len(term) > 1]
    unique = list(dict.fromkeys(terms))
    return " OR ".join('"' + term.replace('"', '""') + '"' for term in unique)


def _merge_ranked(
    exact,
    keyword,
    semantic,
    limit: int,
) -> list[LocalSearchResult]:
    merged: dict[str, dict] = {}
    scores: dict[str, float] = {}
    match_types: dict[str, set[str]] = {}
    for match_type, results, weight in (
        ("exact", exact, 4.0),
        ("keyword", keyword, 1.0),
        ("vector", semantic, 1.0),
    ):
        for rank, (row, _raw_score) in enumerate(results, start=1):
            chunk_id = row["chunk_id"]
            merged.setdefault(chunk_id, row)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (60 + rank)
            match_types.setdefault(chunk_id, set()).add(match_type)
    ordered = sorted(merged, key=lambda item: (-scores[item], item))[:limit]
    return [
        LocalSearchResult(
            chunk_id=chunk_id,
            source_slug=merged[chunk_id]["source_slug"],
            source_name=merged[chunk_id]["source_name"],
            source_type=merged[chunk_id]["source_type"],
            jurisdiction=merged[chunk_id]["jurisdiction"],
            source_url=merged[chunk_id]["source_url"],
            publisher=merged[chunk_id]["publisher"],
            retrieved_at=_iso_timestamp(merged[chunk_id]["retrieved_at"]),
            last_checked_at=_iso_timestamp(merged[chunk_id]["last_checked_at"]),
            effective_from=_optional_iso_timestamp(merged[chunk_id]["effective_from"]),
            effective_to=_optional_iso_timestamp(merged[chunk_id]["effective_to"]),
            citation=merged[chunk_id]["citation"],
            title=merged[chunk_id]["title"],
            text=merged[chunk_id]["text"],
            score=scores[chunk_id],
            match_types=tuple(sorted(match_types[chunk_id])),
        )
        for chunk_id in ordered
    ]


def _iso_timestamp(value: object) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _optional_iso_timestamp(value: object) -> str | None:
    return None if value is None else _iso_timestamp(value)
