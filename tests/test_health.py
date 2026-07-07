from fastapi.testclient import TestClient

from app.main import app


def test_health_returns_ok_when_database_is_healthy(monkeypatch):
    monkeypatch.setattr("app.api.health.database_is_healthy", lambda: True)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_when_database_is_unhealthy(monkeypatch):
    monkeypatch.setattr("app.api.health.database_is_healthy", lambda: False)

    response = TestClient(app).get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "error"}


def test_health_response_does_not_include_database_url(monkeypatch):
    monkeypatch.setattr("app.api.health.database_is_healthy", lambda: True)

    response = TestClient(app).get("/health")

    assert "DATABASE_URL" not in response.text
    assert "postgresql" not in response.text

