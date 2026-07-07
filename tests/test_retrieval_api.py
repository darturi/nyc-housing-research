from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.retrieval_log import RetrievalLog
from tests.retrieval_fixtures import (
    TEST_PASSWORD,
    create_retrieval_corpus,
    create_test_user,
)


def test_search_requires_authentication():
    response = TestClient(app).post("/search", json={"query": "good repair"})

    assert response.status_code == 401


def test_search_returns_results_and_logs_chunk_ids():
    create_retrieval_corpus()
    create_test_user()
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200

    response = client.post(
        "/search",
        json={"query": "NYC Admin Code § 27-2005", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_mode"] == "hybrid"
    assert body["results"]
    assert body["results"][0]["source_url"].startswith("https://")

    with SessionLocal() as db:
        log = db.query(RetrievalLog).one()
        assert log.returned_chunk_ids == [
            result["chunk_id"] for result in body["results"]
        ]


def test_search_uses_default_limit_when_omitted(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "search_default_limit", 1)
    create_retrieval_corpus()
    create_test_user()
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200

    response = client.post("/search", json={"query": "owner heat repair"})

    assert response.status_code == 200
    assert len(response.json()["results"]) <= 1
