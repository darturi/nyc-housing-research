from sqlalchemy import Select

from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from app.retrieval.schemas import SearchFilters, SearchResult

ALLOWED_ACCESS_TYPES = ("public_api", "public_web")
ALLOWED_LICENSE_STATUSES = (
    "open_data",
    "open_license",
    "public_domain",
    "public_official",
    "public_official_terms_reviewed",
)


def apply_filters(query: Select, filters: SearchFilters) -> Select:
    query = query.where(
        Source.is_active.is_(True),
        Source.access_type.in_(ALLOWED_ACCESS_TYPES),
        Source.license_status.in_(ALLOWED_LICENSE_STATUSES),
        SourceVersion.is_current.is_(True),
    )
    if filters.source_type:
        query = query.where(Source.source_type == filters.source_type)
    if filters.jurisdiction:
        query = query.where(Source.jurisdiction == filters.jurisdiction)
    if filters.source_slug:
        query = query.where(Source.slug == filters.source_slug)
    if filters.source_id:
        query = query.where(Source.id == filters.source_id)
    if filters.document_id:
        query = query.where(Document.id == filters.document_id)
    return query


def source_eligibility_sql(alias: str = "s") -> str:
    access_values = ", ".join(f"'{value}'" for value in ALLOWED_ACCESS_TYPES)
    license_values = ", ".join(f"'{value}'" for value in ALLOWED_LICENSE_STATUSES)
    return (
        f"{alias}.is_active = true "
        f"AND {alias}.access_type IN ({access_values}) "
        f"AND {alias}.license_status IN ({license_values})"
    )


def filter_sql(
    filters: SearchFilters,
    source_alias: str = "s",
    document_alias: str = "d",
    source_version_alias: str = "sv",
) -> tuple[str, dict[str, str]]:
    conditions = [
        source_eligibility_sql(source_alias),
        f"{source_version_alias}.is_current = true",
    ]
    params: dict[str, str] = {}
    if filters.source_type:
        conditions.append(f"{source_alias}.source_type = :source_type")
        params["source_type"] = filters.source_type
    if filters.jurisdiction:
        conditions.append(f"{source_alias}.jurisdiction = :jurisdiction")
        params["jurisdiction"] = filters.jurisdiction
    if filters.source_slug:
        conditions.append(f"{source_alias}.slug = :source_slug")
        params["source_slug"] = filters.source_slug
    if filters.source_id:
        conditions.append(f"{source_alias}.id = :source_id")
        params["source_id"] = filters.source_id
    if filters.document_id:
        conditions.append(f"{document_alias}.id = :document_id")
        params["document_id"] = filters.document_id
    return " AND ".join(conditions), params


def row_to_search_result(row, score: float, match_type: str) -> SearchResult:
    chunk, document, source, source_version = row
    return SearchResult(
        chunk_id=chunk.id,
        document_id=document.id,
        source_id=source.id,
        source_version_id=source_version.id,
        source_name=source.name,
        source_type=source.source_type,
        jurisdiction=source.jurisdiction,
        source_url=source_version.source_url or source.source_url,
        citation=chunk.citation,
        title=chunk.title,
        text=chunk.text,
        score=score,
        match_type=match_type,
    )
