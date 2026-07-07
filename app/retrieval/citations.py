from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.ingestion.citations import extract_citations
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from app.retrieval.common import apply_filters, row_to_search_result
from app.retrieval.schemas import SearchFilters, SearchResult


def citation_lookup(
    db: DbSession,
    query_text: str,
    filters: SearchFilters,
    limit: int,
) -> list[SearchResult]:
    candidates = extract_citations(query_text)
    if not candidates:
        return []

    normalized = [candidate.normalized_citation for candidate in candidates]
    query = (
        select(Chunk, Document, Source, SourceVersion)
        .join(Citation, Citation.chunk_id == Chunk.id)
        .join(Document, Document.id == Chunk.document_id)
        .join(Source, Source.id == Chunk.source_id)
        .join(SourceVersion, SourceVersion.id == Chunk.source_version_id)
        .where(Citation.normalized_citation.in_(normalized))
    )
    query = apply_filters(query, filters).limit(limit)
    rows = db.execute(query).all()
    return [row_to_search_result(row, 1.0, "citation") for row in rows]

