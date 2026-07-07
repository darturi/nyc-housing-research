from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, validate_default=True)
    filters: dict[str, str] | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Query cannot be empty.")
        return normalized

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, value: int | None) -> int:
        settings = get_settings()
        if value is None:
            return settings.search_default_limit
        return min(value, settings.search_max_limit)


class SearchResultResponse(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    source_version_id: str
    source_name: str
    source_type: str
    jurisdiction: str
    source_url: str
    citation: str | None
    title: str | None
    text: str
    score: float
    match_type: str


class SearchResponse(BaseModel):
    query: str
    retrieval_mode: str
    results: list[SearchResultResponse]
