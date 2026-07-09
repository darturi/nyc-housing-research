from datetime import timedelta

from app.cli import limits
from app.db.session import SessionLocal
from app.limits.time import utc_now
from app.models.answer_log import AnswerLog
from app.models.rate_limit_event import RateLimitEvent
from tests.retrieval_fixtures import create_test_user


def test_reset_user_command_clears_user_limit_state(capsys):
    user = create_test_user()
    other_user = create_test_user("other@example.com")

    with SessionLocal() as db:
        db.add(
            _answer_log(
                user_id=user.id,
                question_hash="today",
                created_at=utc_now(),
            )
        )
        db.add(
            _answer_log(
                user_id=user.id,
                question_hash="old",
                created_at=utc_now() - timedelta(days=2),
            )
        )
        db.add(
            _answer_log(
                user_id=other_user.id,
                question_hash="other",
                created_at=utc_now(),
            )
        )
        db.add(
            RateLimitEvent(
                scope="user",
                key=user.id,
                event_type="answer_request",
                user_id=user.id,
                event_metadata={},
            )
        )
        db.add(
            RateLimitEvent(
                scope="user",
                key=other_user.id,
                event_type="answer_request",
                user_id=other_user.id,
                event_metadata={},
            )
        )
        db.commit()

    limits.reset_user_command("ADMIN@example.com")

    captured = capsys.readouterr()
    assert "Reset limits for admin@example.com" in captured.out
    assert "deleted 1 rate limit events" in captured.out
    assert "1 answer logs" in captured.out

    with SessionLocal() as db:
        remaining_hashes = {
            log.question_hash for log in db.query(AnswerLog).order_by(AnswerLog.id)
        }
        remaining_event_user_ids = {
            event.user_id
            for event in db.query(RateLimitEvent).order_by(RateLimitEvent.id)
        }

    assert remaining_hashes == {"old", "other"}
    assert remaining_event_user_ids == {other_user.id}


def test_reset_user_command_rejects_unknown_user():
    try:
        limits.reset_user_command("missing@example.com")
    except ValueError as exc:
        assert "User not found: missing@example.com" in str(exc)
    else:
        raise AssertionError("Expected missing user to raise ValueError")


def _answer_log(
    user_id: str,
    question_hash: str,
    created_at,
) -> AnswerLog:
    return AnswerLog(
        user_id=user_id,
        retrieval_log_id=None,
        question_text=f"question {question_hash}",
        question_hash=question_hash,
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
        created_at=created_at,
    )
