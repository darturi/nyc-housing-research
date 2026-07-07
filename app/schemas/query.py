from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings
from app.schemas.answer import AnswerResponse
from app.schemas.hpd import HpdViolationSearchResponse


class RoutedQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, validate_default=True)
    filters: dict[str, str] | None = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Question cannot be empty.")
        return normalized

    @field_validator("limit")
    @classmethod
    def clamp_limit(cls, value: int | None) -> int:
        settings = get_settings()
        if value is None:
            return settings.search_default_limit
        return min(value, settings.search_max_limit)


class RoutedQueryResponse(BaseModel):
    question: str
    route: str
    route_reason: str
    answer: AnswerResponse | None = None
    hpd_violations: HpdViolationSearchResponse | None = None
    message: str | None = None
