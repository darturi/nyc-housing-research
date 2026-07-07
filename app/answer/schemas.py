from dataclasses import dataclass

from app.retrieval.schemas import SearchResult


@dataclass(frozen=True)
class AnswerCitation:
    chunk_id: str
    citation: str | None
    source_name: str
    source_url: str


@dataclass(frozen=True)
class AnswerResult:
    question: str
    answer_status: str
    answer: str
    citations: list[AnswerCitation]
    source_coverage: str | None
    disclaimer: str


@dataclass(frozen=True)
class PromptContext:
    question: str
    chunks: list[SearchResult]
    source_coverage: str | None
    disclaimer: str


@dataclass(frozen=True)
class ProviderAnswer:
    answer_text: str
    cited_chunk_ids: list[str]
    answer_status: str
    prompt_token_count: int | None = None
    completion_token_count: int | None = None
    total_token_count: int | None = None
