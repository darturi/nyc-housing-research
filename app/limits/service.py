from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.limits.schemas import RateLimitDecision
from app.limits.time import utc_now
from app.models.answer_log import AnswerLog
from app.models.rate_limit_event import RateLimitEvent
from app.models.user_quota import UserQuota


def client_ip_from_request(request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def record_event(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
    endpoint: str | None = None,
    user_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RateLimitEvent:
    event = RateLimitEvent(
        scope=scope,
        key=key,
        event_type=event_type,
        endpoint=endpoint,
        user_id=user_id,
        ip_address=ip_address,
        event_metadata=metadata or {},
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def check_and_record_window_event(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
    limit: int,
    window_seconds: int,
    reason: str,
    endpoint: str | None = None,
    user_id: str | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RateLimitDecision:
    decision = check_window_limit(
        db,
        scope,
        key,
        event_type,
        limit,
        window_seconds,
        reason,
    )
    if not decision.allowed:
        db.commit()
        return decision

    event = RateLimitEvent(
        scope=scope,
        key=key,
        event_type=event_type,
        endpoint=endpoint,
        user_id=user_id,
        ip_address=ip_address,
        event_metadata=metadata or {},
    )
    db.add(event)
    db.commit()
    return decision


def count_events(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
    since: datetime,
) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(RateLimitEvent)
            .where(
                RateLimitEvent.scope == scope,
                RateLimitEvent.key == key,
                RateLimitEvent.event_type == event_type,
                RateLimitEvent.created_at >= since,
            )
        )
        or 0
    )


def oldest_event_at(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
    since: datetime,
) -> datetime | None:
    return db.scalar(
        select(RateLimitEvent.created_at)
        .where(
            RateLimitEvent.scope == scope,
            RateLimitEvent.key == key,
            RateLimitEvent.event_type == event_type,
            RateLimitEvent.created_at >= since,
        )
        .order_by(RateLimitEvent.created_at.asc())
        .limit(1)
    )


def check_window_limit(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
    limit: int,
    window_seconds: int,
    reason: str | None = None,
) -> RateLimitDecision:
    _lock_rate_limit_key(db, scope, key, event_type)
    now = utc_now()
    since = now - timedelta(seconds=window_seconds)
    count = count_events(db, scope, key, event_type, since)
    oldest = oldest_event_at(db, scope, key, event_type, since) or now
    reset_at = _aware_datetime(oldest) + timedelta(seconds=window_seconds)
    retry_after_seconds = max(1, int((reset_at - now).total_seconds()))
    remaining = max(0, limit - count)
    return RateLimitDecision(
        allowed=count < limit,
        limit=limit,
        remaining=remaining,
        reset_at=reset_at,
        retry_after_seconds=retry_after_seconds,
        reason=reason or event_type,
    )


def _lock_rate_limit_key(
    db: DbSession,
    scope: str,
    key: str,
    event_type: str,
) -> None:
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    lock_key = f"{scope}:{key}:{event_type}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key)::bigint)"),
        {"lock_key": lock_key},
    )


def check_daily_token_budget(
    db: DbSession,
    user_id: str,
    requested_estimate: int,
    budget: int,
) -> RateLimitDecision:
    now = utc_now()
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    used = (
        db.scalar(
            select(func.coalesce(func.sum(AnswerLog.total_token_count), 0)).where(
                AnswerLog.user_id == user_id,
                AnswerLog.created_at >= day_start,
            )
        )
        or 0
    )
    outstanding = _outstanding_answer_reservations(db, day_start, user_id=user_id)
    used += sum(int(row.event_metadata.get("tokens", 0)) for row in outstanding)
    remaining = max(0, budget - int(used))
    reset_at = day_start + timedelta(days=1)
    retry_after_seconds = max(1, int((reset_at - now).total_seconds()))
    return RateLimitDecision(
        allowed=remaining >= requested_estimate,
        limit=budget,
        remaining=remaining,
        reset_at=reset_at,
        retry_after_seconds=retry_after_seconds,
        reason="daily_llm_token_budget",
    )


def answer_cost_microdollars(prompt_tokens: int, completion_tokens: int) -> int:
    """Return the configured model cost in microdollars, rounded up."""
    settings = get_settings()
    cost_usd = (
        prompt_tokens * settings.answer_llm_input_cost_per_million_tokens_usd
        + completion_tokens * settings.answer_llm_output_cost_per_million_tokens_usd
    ) / 1_000_000
    return ceil(cost_usd * 1_000_000)


def estimate_answer_cost_microdollars() -> int:
    settings = get_settings()
    return answer_cost_microdollars(
        settings.answer_max_context_chars // 4,
        settings.answer_max_output_tokens,
    )


def check_monthly_llm_cost_budget(db: DbSession) -> RateLimitDecision:
    """Apply a global preflight budget before a paid provider request starts."""
    settings = get_settings()
    now = utc_now()
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    _lock_rate_limit_key(db, "global", "llm_cost", "monthly")
    rows = db.execute(
        select(AnswerLog.prompt_token_count, AnswerLog.completion_token_count).where(
            AnswerLog.created_at >= month_start,
            AnswerLog.llm_provider != "fake",
        )
    ).all()
    used = sum(answer_cost_microdollars(row[0] or 0, row[1] or 0) for row in rows)
    used += sum(
        int(row.event_metadata.get("microdollars", 0))
        for row in _outstanding_answer_reservations(db, month_start)
    )
    requested = estimate_answer_cost_microdollars()
    budget = int(round(settings.monthly_llm_cost_budget_usd * 1_000_000))
    remaining = max(0, budget - used)
    if month_start.month == 12:
        reset_at = datetime(month_start.year + 1, 1, 1, tzinfo=UTC)
    else:
        reset_at = datetime(month_start.year, month_start.month + 1, 1, tzinfo=UTC)
    return RateLimitDecision(
        allowed=remaining >= requested,
        limit=budget,
        remaining=remaining,
        reset_at=reset_at,
        retry_after_seconds=max(1, int((reset_at - now).total_seconds())),
        reason="monthly_llm_cost_budget",
    )


def lock_answer_admission(db: DbSession) -> None:
    """Serialize checking and reserving, never the provider's network request."""
    if db.bind is not None and db.bind.dialect.name == "sqlite":
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    else:
        _lock_rate_limit_key(db, "global", "llm_cost", "monthly")


def reserve_answer_usage(db: DbSession, user_id: str, tokens: int) -> RateLimitEvent:
    event = RateLimitEvent(
        scope="user",
        key=user_id,
        user_id=user_id,
        event_type="answer_cost_reserved",
        endpoint="/answer",
        event_metadata={
            "tokens": tokens,
            "microdollars": estimate_answer_cost_microdollars(),
        },
    )
    db.add(event)
    db.flush()
    return event


def finish_answer_usage(
    db: DbSession, reservation_id: str | None, *, completed: bool
) -> None:
    if reservation_id is None:
        return
    event = db.get(RateLimitEvent, reservation_id)
    if event is not None:
        event.event_type = (
            "answer_cost_settled" if completed else "answer_cost_uncertain"
        )
        db.commit()


def _outstanding_answer_reservations(db, since, *, user_id=None):
    statement = select(RateLimitEvent).where(
        RateLimitEvent.event_type.in_(
            ["answer_cost_reserved", "answer_cost_uncertain"]
        ),
        RateLimitEvent.created_at >= since,
    )
    if user_id is not None:
        statement = statement.where(RateLimitEvent.user_id == user_id)
    return list(db.scalars(statement))


def get_user_quota(db: DbSession, user_id: str) -> UserQuota | None:
    return db.scalar(select(UserQuota).where(UserQuota.user_id == user_id))


def search_limit_for_user(db: DbSession, user_id: str) -> int:
    quota = get_user_quota(db, user_id)
    if quota and quota.search_requests_per_hour is not None:
        return quota.search_requests_per_hour
    return get_settings().search_requests_per_hour


def answer_limit_for_user(db: DbSession, user_id: str) -> int:
    quota = get_user_quota(db, user_id)
    if quota and quota.answer_requests_per_hour is not None:
        return quota.answer_requests_per_hour
    return get_settings().answer_requests_per_hour


def token_budget_for_user(db: DbSession, user_id: str) -> int:
    quota = get_user_quota(db, user_id)
    if quota and quota.daily_llm_token_budget is not None:
        return quota.daily_llm_token_budget
    return get_settings().user_daily_llm_token_budget


def user_is_exempt(db: DbSession, user_id: str) -> bool:
    quota = get_user_quota(db, user_id)
    return bool(quota and quota.is_rate_limit_exempt)


def estimate_answer_tokens() -> int:
    settings = get_settings()
    return (settings.answer_max_context_chars // 4) + settings.answer_max_output_tokens


def prune_old_events(db: DbSession, retention_days: int | None = None) -> int:
    settings = get_settings()
    days = retention_days or settings.rate_limit_event_retention_days
    cutoff = utc_now() - timedelta(days=days)
    result = db.execute(
        delete(RateLimitEvent).where(
            RateLimitEvent.created_at < cutoff,
            RateLimitEvent.event_type.not_in(
                ["answer_cost_reserved", "answer_cost_uncertain"]
            ),
        )
    )
    db.commit()
    return result.rowcount or 0


def reset_user_limit_state(db: DbSession, user_id: str) -> tuple[int, int]:
    now = utc_now()
    day_start = datetime(now.year, now.month, now.day, tzinfo=UTC)

    rate_limit_result = db.execute(
        delete(RateLimitEvent).where(RateLimitEvent.user_id == user_id)
    )
    # Daily LLM budget usage is derived from today's answer logs.
    answer_log_result = db.execute(
        delete(AnswerLog).where(
            AnswerLog.user_id == user_id,
            AnswerLog.created_at >= day_start,
        )
    )
    db.commit()
    return rate_limit_result.rowcount or 0, answer_log_result.rowcount or 0


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
