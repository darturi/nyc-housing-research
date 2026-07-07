from time import perf_counter

from sqlalchemy.orm import Session as DbSession

from app.answer.citations import build_public_citations, validate_cited_chunk_ids
from app.answer.logging import log_answer
from app.answer.prompts import (
    DEFAULT_SOURCE_COVERAGE,
    LEGAL_INFORMATION_DISCLAIMER,
    build_prompt,
    trim_context,
)
from app.answer.providers import LLMProviderError, get_answer_provider
from app.answer.schemas import AnswerResult, PromptContext
from app.core.config import get_settings
from app.models.user import User
from app.retrieval.hybrid import hybrid_search
from app.retrieval.logging import log_retrieval
from app.retrieval.schemas import SearchFilters

UNSUPPORTED_ANSWER = (
    "The current corpus does not contain enough retrieved public-source "
    "material to answer that question."
)


def generate_answer(
    db: DbSession,
    current_user: User,
    question: str,
    filters: SearchFilters,
    limit: int,
) -> AnswerResult:
    settings = get_settings()
    provider = get_answer_provider()
    started = perf_counter()

    retrieval_started = perf_counter()
    retrieved_results = hybrid_search(db, question, filters, limit)
    retrieval_latency_ms = int((perf_counter() - retrieval_started) * 1000)
    retrieval_log = log_retrieval(
        db,
        current_user.id,
        question,
        filters,
        "hybrid",
        retrieved_results,
        retrieval_latency_ms,
    )

    source_coverage = (
        DEFAULT_SOURCE_COVERAGE if settings.answer_include_source_coverage else None
    )
    if not retrieved_results:
        latency_ms = int((perf_counter() - started) * 1000)
        log_answer(
            db,
            current_user.id,
            retrieval_log.id,
            question,
            filters,
            retrieved_results,
            [],
            UNSUPPORTED_ANSWER,
            "unsupported",
            provider.provider_name,
            provider.model_name,
            latency_ms,
        )
        return AnswerResult(
            question=question,
            answer_status="unsupported",
            answer=UNSUPPORTED_ANSWER,
            citations=[],
            source_coverage=source_coverage,
            disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        )

    context_chunks = trim_context(
        retrieved_results,
        settings.answer_max_context_chunks,
        settings.answer_max_context_chars,
    )
    prompt = build_prompt(
        PromptContext(
            question=question,
            chunks=context_chunks,
            source_coverage=source_coverage,
            disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        )
    )

    try:
        provider_answer = provider.generate(prompt, question, context_chunks)
    except LLMProviderError as exc:
        latency_ms = int((perf_counter() - started) * 1000)
        log_answer(
            db,
            current_user.id,
            retrieval_log.id,
            question,
            filters,
            retrieved_results,
            [],
            "",
            "provider_error",
            provider.provider_name,
            provider.model_name,
            latency_ms,
            error_message=str(exc),
        )
        raise

    cited_chunk_ids = validate_cited_chunk_ids(
        provider_answer.cited_chunk_ids,
        context_chunks,
    )
    answer_status = provider_answer.answer_status
    answer_text = provider_answer.answer_text
    if answer_status == "answered" and not cited_chunk_ids:
        answer_status = "unsupported"
        answer_text = UNSUPPORTED_ANSWER

    public_citations = build_public_citations(cited_chunk_ids, context_chunks)
    latency_ms = int((perf_counter() - started) * 1000)
    log_answer(
        db,
        current_user.id,
        retrieval_log.id,
        question,
        filters,
        retrieved_results,
        cited_chunk_ids,
        answer_text,
        answer_status,
        provider.provider_name,
        provider.model_name,
        latency_ms,
        provider_answer,
    )
    return AnswerResult(
        question=question,
        answer_status=answer_status,
        answer=answer_text,
        citations=public_citations,
        source_coverage=source_coverage,
        disclaimer=LEGAL_INFORMATION_DISCLAIMER,
    )
