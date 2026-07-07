from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app
from tests.retrieval_fixtures import TEST_PASSWORD, create_test_user


def test_answer_timeout_returns_504(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "answer_timeout_seconds", 10)
    create_test_user()
    client = _authenticated_client()
    times = iter([0.0, 11.0])
    monkeypatch.setattr("app.api.answers.perf_counter", lambda: next(times))

    response = client.post("/answer", json={"question": "unsupported"})

    assert response.status_code == 504
    assert response.json()["detail"] == "Request timed out."


def test_search_timeout_returns_504(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "search_timeout_seconds", 10)
    create_test_user()
    client = _authenticated_client()
    times = iter([0.0, 11.0])
    monkeypatch.setattr("app.api.search.perf_counter", lambda: next(times))

    response = client.post("/search", json={"query": "anything"})

    assert response.status_code == 504
    assert response.json()["detail"] == "Request timed out."


def _authenticated_client() -> TestClient:
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return client
