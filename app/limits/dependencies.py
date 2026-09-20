from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.limits.errors import rate_limit_exception
from app.limits.service import (
    answer_limit_for_user,
    check_and_record_window_event,
    check_daily_token_budget,
    check_monthly_llm_cost_budget,
    check_window_limit,
    client_ip_from_request,
    estimate_answer_tokens,
    lock_answer_admission,
    record_event,
    reserve_answer_usage,
    search_limit_for_user,
    token_budget_for_user,
    user_is_exempt,
)
from app.models.user import User


def enforce_login_limit(request: Request, db: DbSession) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    ip_address = client_ip_from_request(request)
    decision = check_window_limit(
        db,
        "ip",
        ip_address,
        "login_failed",
        settings.login_failed_limit,
        settings.login_failed_window_seconds,
        "login_failed",
    )
    if not decision.allowed:
        record_event(
            db,
            "ip",
            ip_address,
            "request_rejected",
            endpoint="/auth/login",
            ip_address=ip_address,
            metadata={"reason": decision.reason},
        )
        raise rate_limit_exception(decision)


def record_failed_login(request: Request, db: DbSession) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    ip_address = client_ip_from_request(request)
    decision = check_and_record_window_event(
        db,
        "ip",
        ip_address,
        "login_failed",
        settings.login_failed_limit,
        settings.login_failed_window_seconds,
        "login_failed",
        endpoint="/auth/login",
        ip_address=ip_address,
    )
    if not decision.allowed:
        raise rate_limit_exception(decision)


def enforce_search_limit(
    request: Request,
    db: DbSession,
    current_user: User,
) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled or user_is_exempt(db, current_user.id):
        return
    decision = check_and_record_window_event(
        db,
        "user",
        current_user.id,
        "search_request",
        search_limit_for_user(db, current_user.id),
        3600,
        "search_requests_per_hour",
        endpoint="/search",
        user_id=current_user.id,
        ip_address=client_ip_from_request(request),
    )
    if not decision.allowed:
        _record_authenticated_rejection(request, db, current_user, decision.reason)
        raise rate_limit_exception(decision)


def enforce_answer_limit(
    request: Request,
    db: DbSession,
    current_user: User,
) -> str | None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    lock_answer_admission(db)
    if not user_is_exempt(db, current_user.id):
        decision = check_window_limit(
            db,
            "user",
            current_user.id,
            "answer_request",
            answer_limit_for_user(db, current_user.id),
            3600,
            "answer_requests_per_hour",
        )
        if not decision.allowed:
            _record_authenticated_rejection(request, db, current_user, decision.reason)
            raise rate_limit_exception(decision)

        budget_decision = check_daily_token_budget(
            db,
            current_user.id,
            estimate_answer_tokens(),
            token_budget_for_user(db, current_user.id),
        )
        if not budget_decision.allowed:
            _record_authenticated_rejection(
                request,
                db,
                current_user,
                budget_decision.reason,
            )
            raise rate_limit_exception(budget_decision)

    monthly_decision = check_monthly_llm_cost_budget(db)
    if not monthly_decision.allowed:
        _record_authenticated_rejection(
            request,
            db,
            current_user,
            monthly_decision.reason,
        )
        raise rate_limit_exception(monthly_decision)

    reservation = reserve_answer_usage(db, current_user.id, estimate_answer_tokens())
    record_event(
        db,
        "user",
        current_user.id,
        "answer_request",
        endpoint="/answer",
        user_id=current_user.id,
        ip_address=client_ip_from_request(request),
    )
    return reservation.id


def raise_timeout() -> None:
    raise HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail="Request timed out.",
    )


def _record_authenticated_rejection(
    request: Request,
    db: DbSession,
    current_user: User,
    reason: str,
) -> None:
    record_event(
        db,
        "user",
        current_user.id,
        "request_rejected",
        endpoint=request.url.path,
        user_id=current_user.id,
        ip_address=client_ip_from_request(request),
        metadata={"reason": reason},
    )
