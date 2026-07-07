from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.rate_limit_event import RateLimitEvent
from tests.retrieval_fixtures import (
    TEST_PASSWORD,
    create_retrieval_corpus,
    create_test_user,
)


def test_search_requests_over_hourly_limit_return_429(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "search_requests_per_hour", 1)
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    first = client.post("/search", json={"query": "good repair"})
    blocked = client.post("/search", json={"query": "heat"})

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["reason"] == "search_requests_per_hour"


def test_answer_requests_over_hourly_limit_return_429(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "answer_requests_per_hour", 1)
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    first = client.post("/answer", json={"question": "good repair"})
    blocked = client.post("/answer", json={"question": "heat"})

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["reason"] == "answer_requests_per_hour"


def test_daily_token_budget_blocks_answer_before_generation(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "user_daily_llm_token_budget", 1)
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    response = client.post("/answer", json={"question": "good repair"})

    assert response.status_code == 429
    assert response.json()["detail"]["reason"] == "daily_llm_token_budget"
    with SessionLocal() as db:
        events = db.query(RateLimitEvent).all()
        assert [event.event_type for event in events] == ["request_rejected"]


def _authenticated_client() -> TestClient:
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return client
