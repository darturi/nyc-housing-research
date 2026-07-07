import hashlib

from sqlalchemy.orm import Session as DbSession

from app.models.retrieval_log import RetrievalLog
from app.retrieval.schemas import SearchFilters, SearchResult


def hash_query(query_text: str) -> str:
    normalized = " ".join(query_text.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def log_retrieval(
    db: DbSession,
    user_id: str | None,
    query_text: str,
    filters: SearchFilters,
    retrieval_mode: str,
    results: list[SearchResult],
    latency_ms: int,
) -> RetrievalLog:
    log = RetrievalLog(
        user_id=user_id,
        query_text=query_text,
        query_hash=hash_query(query_text),
        filters=filters.as_dict(),
        retrieval_mode=retrieval_mode,
        returned_chunk_ids=[result.chunk_id for result in results],
        result_count=len(results),
        latency_ms=latency_ms,
    )
    db.add(log)
    db.commit()
    db.refresh(log)
    return log

