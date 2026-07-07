from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.db.session import get_db, set_statement_timeout
from app.limits.dependencies import enforce_search_limit, raise_timeout
from app.models.user import User
from app.retrieval.hybrid import hybrid_search
from app.retrieval.logging import log_retrieval
from app.retrieval.schemas import SearchFilters
from app.schemas.search import SearchRequest, SearchResponse, SearchResultResponse

router = APIRouter(tags=["search"])
DbDependency = Annotated[DbSession, Depends(get_db)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


@router.post("/search", response_model=SearchResponse)
def search(
    payload: SearchRequest,
    request: Request,
    db: DbDependency,
    current_user: CurrentUserDependency,
) -> SearchResponse:
    try:
        filters = SearchFilters.from_dict(payload.filters)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    enforce_search_limit(request, db, current_user)
    settings = get_settings()
    set_statement_timeout(db, settings.search_timeout_seconds)
    started = perf_counter()
    results = hybrid_search(db, payload.query, filters, payload.limit)
    latency_ms = int((perf_counter() - started) * 1000)
    if latency_ms > settings.search_timeout_seconds * 1000:
        raise_timeout()
    log_retrieval(
        db,
        current_user.id,
        payload.query,
        filters,
        "hybrid",
        results,
        latency_ms,
    )
    return SearchResponse(
        query=payload.query,
        retrieval_mode="hybrid",
        results=[
            SearchResultResponse(**result.__dict__)
            for result in results
        ],
    )
