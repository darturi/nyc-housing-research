from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.models.chunk import Chunk
from app.models.chunk_embedding import ChunkEmbedding
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from app.retrieval.common import apply_filters, filter_sql, row_to_search_result
from app.retrieval.embeddings import cosine_similarity, get_embedding_provider
from app.retrieval.schemas import SearchFilters, SearchResult


def vector_search(
    db: DbSession,
    query_text: str,
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    settings = get_settings()
    query_vector = get_embedding_provider().embed_text(query_text)
    if _pgvector_available(db):
        return _postgres_vector_search(db, query_vector, filters, limit)

    query = (
        select(Chunk, Document, Source, SourceVersion, ChunkEmbedding)
        .join(Document, Document.id == Chunk.document_id)
        .join(Source, Source.id == Chunk.source_id)
        .join(SourceVersion, SourceVersion.id == Chunk.source_version_id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == Chunk.id)
        .where(ChunkEmbedding.embedding_model == settings.embedding_model)
    )
    rows = db.execute(apply_filters(query, filters)).all()
    results: list[SearchResult] = []
    for row in rows:
        score = cosine_similarity(query_vector, row[4].embedding)
        if score <= 0:
            continue
        results.append(row_to_search_result(row[:4], score, "vector"))
    return sorted(results, key=lambda result: (-result.score, result.chunk_id))[:limit]


def _pgvector_available(db: DbSession) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    columns = inspect(db.get_bind()).get_columns("chunk_embeddings")
    return any(column["name"] == "embedding_vector" for column in columns)


def _postgres_vector_search(
    db: DbSession,
    query_vector: list[float],
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    settings = get_settings()
    where_sql, params = filter_sql(filters)
    params.update(
        {
            "embedding_model": settings.embedding_model,
            "limit": limit,
            "query_vector": vector_literal(query_vector),
        }
    )
    rows = db.execute(
        text(
            f"""
            SELECT
                c.id AS chunk_id,
                d.id AS document_id,
                s.id AS source_id,
                sv.id AS source_version_id,
                s.name AS source_name,
                s.source_type AS source_type,
                s.jurisdiction AS jurisdiction,
                COALESCE(d.source_url, sv.source_url, s.source_url) AS source_url,
                c.citation AS citation,
                c.title AS title,
                c.text AS chunk_text,
                1.0 - (ce.embedding_vector <=> CAST(:query_vector AS vector)) AS score
            FROM chunk_embeddings ce
            JOIN chunks c ON c.id = ce.chunk_id
            JOIN documents d ON d.id = c.document_id
            JOIN sources s ON s.id = c.source_id
            JOIN source_versions sv ON sv.id = c.source_version_id
            WHERE ce.embedding_model = :embedding_model
              AND ce.embedding_vector IS NOT NULL
              AND {where_sql}
            ORDER BY ce.embedding_vector <=> CAST(:query_vector AS vector), c.id
            LIMIT :limit
            """
        ),
        params,
    ).mappings()
    return [
        SearchResult(
            chunk_id=row["chunk_id"],
            document_id=row["document_id"],
            source_id=row["source_id"],
            source_version_id=row["source_version_id"],
            source_name=row["source_name"],
            source_type=row["source_type"],
            jurisdiction=row["jurisdiction"],
            source_url=row["source_url"],
            citation=row["citation"],
            title=row["title"],
            text=row["chunk_text"],
            score=float(row["score"]),
            match_type="vector",
        )
        for row in rows
        if row["score"] > 0
    ]


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.12g}" for value in vector) + "]"
