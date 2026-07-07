from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.main import app
from app.models.answer_log import AnswerLog
from app.models.retrieval_log import RetrievalLog
from tests.retrieval_fixtures import (
    TEST_PASSWORD,
    create_retrieval_corpus,
    create_rpapl_corpus_with_guidance_noise,
    create_test_user,
)


def test_answer_requires_authentication():
    response = TestClient(app).post("/answer", json={"question": "good repair"})

    assert response.status_code == 401


def test_answer_returns_cited_answer_and_logs_generation():
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/answer",
        json={"question": "NYC Admin Code § 27-2005", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer_status"] == "answered"
    assert "good repair" in body["answer"].lower()
    assert body["citations"]
    assert body["citations"][0]["source_url"].startswith("https://")
    assert body["source_coverage"]
    assert "not legal advice" in body["disclaimer"]

    with SessionLocal() as db:
        answer_log = db.query(AnswerLog).one()
        retrieval_log = db.query(RetrievalLog).one()
        assert answer_log.retrieval_log_id == retrieval_log.id
        assert answer_log.answer_status == "answered"
        assert answer_log.cited_chunk_ids == [
            citation["chunk_id"] for citation in body["citations"]
        ]
        assert answer_log.total_token_count is not None


def test_answer_summarizes_retrieved_section_without_dumping_full_text():
    create_rpapl_corpus_with_guidance_noise()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/answer",
        json={"question": "What does RPAPL section 711 cover?", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer_status"] == "answered"
    assert body["citations"][0]["citation"] == "RPAPL § 711"
    assert "Grounds where landlord-tenant relationship exists" in body["answer"]
    assert "It addresses:" in body["answer"]
    assert "A tenant shall include an occupant" not in body["answer"]


def test_answer_uses_default_limit_when_omitted(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "answer_max_context_chunks", 1)
    monkeypatch.setattr(settings, "search_default_limit", 5)
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/answer",
        json={"question": "NYC Admin Code § 27-2005"},
    )

    assert response.status_code == 200
    assert len(response.json()["citations"]) <= 1


def test_answer_returns_unsupported_for_no_results():
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/answer",
        json={"question": "What does this unpublished court case hold?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["answer_status"] == "unsupported"
    assert body["citations"] == []
    assert "does not contain enough" in body["answer"]
    assert "not legal advice" in body["disclaimer"]

    with SessionLocal() as db:
        answer_log = db.query(AnswerLog).one()
        assert answer_log.answer_status == "unsupported"
        assert answer_log.retrieved_chunk_ids == []
        assert answer_log.cited_chunk_ids == []


def test_answer_rejects_unsupported_filters():
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/answer",
        json={
            "question": "good repair",
            "filters": {"unsupported": "value"},
        },
    )

    assert response.status_code == 422


def _authenticated_client() -> TestClient:
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return client
