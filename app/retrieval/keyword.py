from sqlalchemy import select, text
from sqlalchemy.orm import Session as DbSession

from app.models.chunk import Chunk
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from app.retrieval.common import apply_filters, filter_sql, row_to_search_result
from app.retrieval.schemas import SearchFilters, SearchResult


def keyword_score(query_text: str, chunk: Chunk) -> float:
    terms = [term for term in query_text.lower().split() if term]
    if not terms:
        return 0.0
    haystack = " ".join(
        value or "" for value in [chunk.title, chunk.citation, chunk.text]
    ).lower()
    matches = sum(1 for term in terms if term in haystack)
    title_boost = sum(1 for term in terms if term in (chunk.title or "").lower())
    citation_boost = sum(1 for term in terms if term in (chunk.citation or "").lower())
    return (matches + title_boost + citation_boost) / max(len(terms), 1)


def keyword_search(
    db: DbSession,
    query_text: str,
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    if db.get_bind().dialect.name == "postgresql":
        return _postgres_keyword_search(db, query_text, filters, limit)

    query = (
        select(Chunk, Document, Source, SourceVersion)
        .join(Document, Document.id == Chunk.document_id)
        .join(Source, Source.id == Chunk.source_id)
        .join(SourceVersion, SourceVersion.id == Chunk.source_version_id)
    )
    rows = db.execute(apply_filters(query, filters)).all()
    scored = [
        row_to_search_result(row, keyword_score(query_text, row[0]), "keyword")
        for row in rows
    ]
    scored = [result for result in scored if result.score > 0]
    return sorted(scored, key=lambda result: (-result.score, result.chunk_id))[:limit]


def _postgres_keyword_search(
    db: DbSession,
    query_text: str,
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    where_sql, params = filter_sql(filters)
    params.update({"query_text": query_text, "limit": limit})
    rows = db.execute(
        text(
            f"""
            WITH search_query AS (
                SELECT plainto_tsquery('english', :query_text) AS query
            ),
            ranked_chunks AS (
                SELECT
                    c.id AS chunk_id,
                    d.id AS document_id,
                    s.id AS source_id,
                    sv.id AS source_version_id,
                    s.name AS source_name,
                    s.source_type AS source_type,
                    s.jurisdiction AS jurisdiction,
                    COALESCE(sv.source_url, s.source_url) AS source_url,
                    c.citation AS citation,
                    c.title AS title,
                    c.text AS chunk_text,
                    ts_rank_cd(
                        setweight(to_tsvector('english', COALESCE(c.title, '')), 'A')
                        || setweight(
                            to_tsvector('english', COALESCE(c.citation, '')),
                            'A'
                        )
                        || setweight(to_tsvector('english', COALESCE(c.text, '')), 'B'),
                        search_query.query
                    ) AS score
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                JOIN sources s ON s.id = c.source_id
                JOIN source_versions sv ON sv.id = c.source_version_id
                CROSS JOIN search_query
                WHERE {where_sql}
                  AND (
                        setweight(to_tsvector('english', COALESCE(c.title, '')), 'A')
                        || setweight(
                            to_tsvector('english', COALESCE(c.citation, '')),
                            'A'
                        )
                        || setweight(to_tsvector('english', COALESCE(c.text, '')), 'B')
                  ) @@ search_query.query
            )
            SELECT *
            FROM ranked_chunks
            WHERE score > 0
            ORDER BY score DESC, chunk_id
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
            match_type="keyword",
        )
        for row in rows
    ]
