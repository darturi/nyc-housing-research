from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app


def test_large_request_body_returns_413(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_request_body_bytes", 10)
    client = TestClient(app)

    response = client.post(
        "/search",
        content='{"query":"this body is too large"}',
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large."


def test_large_request_body_is_checked_after_header(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_request_body_bytes", 20)
    client = TestClient(app)

    response = client.post(
        "/search",
        content='{"query":"this body is too large"}',
        headers={"content-type": "application/json", "content-length": "10"},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Request body too large."


def test_health_is_not_blocked_by_body_size_middleware(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_request_body_bytes", 1)
    client = TestClient(app)

    response = client.get("/health", headers={"content-length": "999"})

    assert response.status_code == 200
