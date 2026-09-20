from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.answer.providers import LLMProviderError
from app.answer.service import generate_answer
from app.auth.dependencies import get_current_user
from app.core.config import get_settings
from app.db.session import get_db, set_statement_timeout
from app.limits.dependencies import enforce_answer_limit, raise_timeout
from app.limits.service import finish_answer_usage
from app.models.user import User
from app.retrieval.schemas import SearchFilters
from app.schemas.answer import (
    AnswerCitationResponse,
    AnswerRequest,
    AnswerResponse,
)

router = APIRouter(tags=["answers"])
DbDependency = Annotated[DbSession, Depends(get_db)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


@router.post("/answer", response_model=AnswerResponse)
def answer(
    payload: AnswerRequest,
    request: Request,
    db: DbDependency,
    current_user: CurrentUserDependency,
) -> AnswerResponse:
    try:
        filters = SearchFilters.from_dict(payload.filters)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    reservation_id = None
    completed = False
    try:
        reservation_id = enforce_answer_limit(request, db, current_user)
        settings = get_settings()
        set_statement_timeout(db, settings.answer_timeout_seconds)
        started = perf_counter()
        result = generate_answer(
            db,
            current_user,
            payload.question,
            filters,
            payload.limit,
        )
        completed = True
        if (perf_counter() - started) > settings.answer_timeout_seconds:
            raise_timeout()
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Answer provider is unavailable.",
        ) from exc
    finally:
        finish_answer_usage(db, reservation_id, completed=completed)

    return AnswerResponse(
        question=result.question,
        answer_status=result.answer_status,
        answer=result.answer,
        citations=[
            AnswerCitationResponse(**citation.__dict__) for citation in result.citations
        ],
        source_coverage=result.source_coverage,
        disclaimer=result.disclaimer,
    )
