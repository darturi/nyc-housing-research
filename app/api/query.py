from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.answer.providers import LLMProviderError
from app.answer.service import generate_answer
from app.auth.dependencies import get_current_user
from app.db.session import get_db
from app.hpd.search import search_hpd_violations
from app.limits.dependencies import enforce_answer_limit, enforce_search_limit
from app.models.user import User
from app.query_routing.router import classify_query, hpd_request_from_question
from app.retrieval.schemas import SearchFilters
from app.schemas.answer import AnswerCitationResponse, AnswerResponse
from app.schemas.query import RoutedQueryRequest, RoutedQueryResponse

router = APIRouter(tags=["query"])
DbDependency = Annotated[DbSession, Depends(get_db)]
CurrentUserDependency = Annotated[User, Depends(get_current_user)]


@router.post("/query", response_model=RoutedQueryResponse)
def routed_query(
    payload: RoutedQueryRequest,
    request: Request,
    db: DbDependency,
    current_user: CurrentUserDependency,
) -> RoutedQueryResponse:
    route = classify_query(payload.question)
    if route.kind == "property":
        enforce_search_limit(request, db, current_user)
        try:
            hpd_request = hpd_request_from_question(payload.question, payload.limit)
        except ValueError as exc:
            return RoutedQueryResponse(
                question=payload.question,
                route=route.kind,
                route_reason=route.reason,
                message=str(exc),
            )
        return RoutedQueryResponse(
            question=payload.question,
            route=route.kind,
            route_reason=route.reason,
            hpd_violations=search_hpd_violations(db, hpd_request),
        )

    try:
        filters = SearchFilters.from_dict(payload.filters)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    try:
        enforce_answer_limit(request, db, current_user)
        answer_result = generate_answer(
            db,
            current_user,
            payload.question,
            filters,
            payload.limit,
        )
    except LLMProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Answer provider is unavailable.",
        ) from exc

    return RoutedQueryResponse(
        question=payload.question,
        route=route.kind,
        route_reason=route.reason,
        answer=AnswerResponse(
            question=answer_result.question,
            answer_status=answer_result.answer_status,
            answer=answer_result.answer,
            citations=[
                AnswerCitationResponse(**citation.__dict__)
                for citation in answer_result.citations
            ],
            source_coverage=answer_result.source_coverage,
            disclaimer=answer_result.disclaimer,
        ),
    )
