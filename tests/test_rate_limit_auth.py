from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.main import app
from app.models.rate_limit_event import RateLimitEvent
from tests.retrieval_fixtures import TEST_PASSWORD, create_test_user


def test_failed_login_limit_returns_429(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "login_failed_limit", 2)
    create_test_user()
    client = TestClient(app)

    first = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong password value"},
    )
    second = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong password value"},
    )
    blocked = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong password value"},
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["reason"] == "login_failed"
    assert "Retry-After" in blocked.headers

    with SessionLocal() as db:
        events = db.query(RateLimitEvent).all()
        assert [event.event_type for event in events] == [
            "login_failed",
            "login_failed",
            "request_rejected",
        ]


def test_successful_login_works_before_failed_limit(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "login_failed_limit", 2)
    create_test_user()
    client = TestClient(app)

    failed = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": "wrong password value"},
    )
    success = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )

    assert failed.status_code == 401
    assert success.status_code == 200
