from sqlalchemy.orm import Session as DbSession

from app.answer.schemas import ProviderAnswer
from app.models.answer_log import AnswerLog
from app.retrieval.logging import hash_query
from app.retrieval.schemas import SearchFilters, SearchResult


def log_answer(
    db: DbSession,
    user_id: str | None,
    retrieval_log_id: str | None,
    question_text: str,
    filters: SearchFilters,
    retrieved_results: list[SearchResult],
    cited_chunk_ids: list[str],
    answer_text: str,
    answer_status: str,
    llm_provider: str,
    llm_model: str,
    latency_ms: int,
    provider_answer: ProviderAnswer | None = None,
    error_message: str | None = None,
) -> AnswerLog:
    log = AnswerLog(
        user_id=user_id,
        retrieval_log_id=retrieval_log_id,
        question_text=question_text,
        question_hash=hash_query(question_text),
        filters=filters.as_dict(),
        retrieved_chunk_ids=[result.chunk_id for result in retrieved_results],
        cited_chunk_ids=cited_chunk_ids,
        answer_text=answer_text,
        answer_status=answer_status,
        llm_provider=llm_provider,
        llm_model=llm_model,
        prompt_token_count=(
            provider_answer.prompt_token_count if provider_answer else None
        ),
        completion_token_count=(
            provider_answer.completion_token_count if provider_answer else None
        ),
        total_token_count=(
            provider_answer.total_token_count if provider_answer else None
        ),
        latency_ms=latency_ms,
        error_message=error_message,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log
