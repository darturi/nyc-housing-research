from datetime import timedelta

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.limits.service import (
    check_daily_token_budget,
    check_monthly_llm_cost_budget,
    check_window_limit,
    record_event,
)
from app.limits.time import utc_now
from app.models.answer_log import AnswerLog
from tests.retrieval_fixtures import create_test_user


def test_window_limit_counts_only_events_inside_window():
    with SessionLocal() as db:
        record_event(db, "user", "user-1", "search_request")
        old_event = record_event(db, "user", "user-1", "search_request")
        old_event.created_at = utc_now() - timedelta(hours=2)
        db.commit()

        decision = check_window_limit(
            db,
            "user",
            "user-1",
            "search_request",
            limit=2,
            window_seconds=3600,
            reason="search_requests_per_hour",
        )

    assert decision.allowed
    assert decision.remaining == 1
    assert decision.reason == "search_requests_per_hour"


def test_window_limit_blocks_at_limit():
    with SessionLocal() as db:
        record_event(db, "ip", "127.0.0.1", "login_failed")

        decision = check_window_limit(
            db,
            "ip",
            "127.0.0.1",
            "login_failed",
            limit=1,
            window_seconds=900,
        )

    assert not decision.allowed
    assert decision.remaining == 0
    assert decision.retry_after_seconds > 0


def test_daily_token_budget_uses_answer_logs():
    user = create_test_user()
    with SessionLocal() as db:
        db.add(
            AnswerLog(
                user_id=user.id,
                retrieval_log_id=None,
                question_text="question",
                question_hash="hash",
                filters={},
                retrieved_chunk_ids=[],
                cited_chunk_ids=[],
                answer_text="answer",
                answer_status="answered",
                llm_provider="fake",
                llm_model="fake",
                prompt_token_count=50,
                completion_token_count=25,
                total_token_count=75,
                latency_ms=1,
                error_message=None,
            )
        )
        db.commit()

        decision = check_daily_token_budget(
            db,
            user.id,
            requested_estimate=30,
            budget=100,
        )

    assert not decision.allowed
    assert decision.remaining == 25
    assert decision.reason == "daily_llm_token_budget"


def test_monthly_cost_budget_uses_paid_answer_logs(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "monthly_llm_cost_budget_usd", 0.005)
    with SessionLocal() as db:
        db.add(
            AnswerLog(
                user_id=None,
                retrieval_log_id=None,
                question_text="question",
                question_hash="cost-hash",
                filters={},
                retrieved_chunk_ids=[],
                cited_chunk_ids=[],
                answer_text="answer",
                answer_status="answered",
                llm_provider="openai",
                llm_model="gpt-5-mini-2025-08-07",
                prompt_token_count=4000,
                completion_token_count=900,
                total_token_count=4900,
                latency_ms=1,
                error_message=None,
            )
        )
        db.commit()
        decision = check_monthly_llm_cost_budget(db)

    assert not decision.allowed
    assert decision.reason == "monthly_llm_cost_budget"
