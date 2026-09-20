import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import Base
from app.limits.dependencies import enforce_answer_limit
from app.limits.service import (
    estimate_answer_cost_microdollars,
    estimate_answer_tokens,
    finish_answer_usage,
)
from app.models.rate_limit_event import RateLimitEvent
from app.models.user import User


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
@pytest.mark.parametrize("budget", ["daily", "monthly"])
def test_concurrent_hosted_answers_reserve_remaining_budget(
    tmp_path, monkeypatch, backend, budget
):
    schema = "audit_" + uuid.uuid4().hex
    admin = None
    if backend == "postgresql":
        url = os.environ.get("NYC_HOUSING_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Dedicated PostgreSQL test service is not configured.")
        admin = create_engine(url)
        if admin.dialect.name != "postgresql":
            pytest.fail("NYC_HOUSING_TEST_POSTGRES_URL must name PostgreSQL.")
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
        engine = create_engine(
            f"sqlite+pysqlite:///{tmp_path / 'hosted.sqlite3'}",
            connect_args={"timeout": 5},
        )
    Base.metadata.create_all(engine)
    try:
        get_settings.cache_clear()
        if budget == "daily":
            monkeypatch.setenv(
                "USER_DAILY_LLM_TOKEN_BUDGET", str(estimate_answer_tokens())
            )
        else:
            cost = Decimal(estimate_answer_cost_microdollars()) / 1_000_000
            monkeypatch.setenv("MONTHLY_LLM_COST_BUDGET_USD", str(cost))
        get_settings.cache_clear()
        with Session(engine) as db:
            user = User(
                email="budget@example.test", password_hash="unused", is_admin=False
            )
            db.add(user)
            db.commit()
            user_id = user.id
        rendezvous = threading.Barrier(2)

        def admit():
            with Session(engine) as db:
                user = db.get(User, user_id)
                request = Request(
                    {
                        "type": "http",
                        "path": "/answer",
                        "headers": [],
                        "client": ("127.0.0.1", 1),
                    }
                )
                rendezvous.wait(timeout=5)
                try:
                    return enforce_answer_limit(request, db, user)
                except HTTPException as exc:
                    assert exc.status_code == 429
                    assert exc.detail["reason"] == (
                        "daily_llm_token_budget"
                        if budget == "daily"
                        else "monthly_llm_cost_budget"
                    )
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: admit(), range(2)))
        assert sum(result is not None for result in results) == 1
        reservation_id = next(result for result in results if result is not None)
        with Session(engine) as db:
            pending = list(
                db.scalars(
                    select(RateLimitEvent).where(
                        RateLimitEvent.event_type == "answer_cost_reserved"
                    )
                )
            )
            assert len(pending) == 1
            finish_answer_usage(db, reservation_id, completed=True)
            assert (
                db.get(RateLimitEvent, reservation_id).event_type
                == "answer_cost_settled"
            )
    finally:
        get_settings.cache_clear()
        engine.dispose()
        if admin is not None:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()
