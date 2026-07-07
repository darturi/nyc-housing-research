from pydantic import BaseModel, Field, field_validator

from app.core.config import get_settings


class AnswerRequest(BaseModel):
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
            return min(
                settings.answer_max_context_chunks,
                settings.search_default_limit,
            )
        return min(
            value,
            settings.answer_max_context_chunks,
            settings.search_max_limit,
        )


class AnswerCitationResponse(BaseModel):
    chunk_id: str
    citation: str | None
    source_name: str
    source_url: str


class AnswerResponse(BaseModel):
    question: str
    answer_status: str
    answer: str
    citations: list[AnswerCitationResponse]
    source_coverage: str | None
    disclaimer: str
