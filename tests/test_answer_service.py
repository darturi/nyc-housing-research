from app.answer.providers import AnswerProvider, LLMProviderError
from app.answer.schemas import ProviderAnswer
from app.answer.service import generate_answer
from app.db.session import SessionLocal
from app.models.answer_log import AnswerLog
from app.models.chunk import Chunk
from app.retrieval.schemas import SearchFilters, SearchResult
from tests.retrieval_fixtures import (
    create_hmc_corpus_with_superseded_version,
    create_retrieval_corpus,
    create_test_user,
)


def test_answer_with_invalid_model_citations_becomes_unsupported(monkeypatch):
    create_retrieval_corpus()
    user = create_test_user()

    monkeypatch.setattr(
        "app.answer.service.get_answer_provider",
        lambda: InvalidCitationProvider(),
    )

    with SessionLocal() as db:
        result = generate_answer(
            db,
            user,
            "NYC Admin Code § 27-2005",
            SearchFilters(),
            5,
        )

    assert result.answer_status == "unsupported"
    assert result.citations == []

    with SessionLocal() as db:
        answer_log = db.query(AnswerLog).one()
        assert answer_log.answer_status == "unsupported"
        assert answer_log.cited_chunk_ids == []


def test_provider_error_is_logged_and_reraised(monkeypatch):
    create_retrieval_corpus()
    user = create_test_user()

    monkeypatch.setattr(
        "app.answer.service.get_answer_provider",
        lambda: FailingProvider(),
    )

    with SessionLocal() as db:
        try:
            generate_answer(
                db,
                user,
                "NYC Admin Code § 27-2005",
                SearchFilters(),
                5,
            )
        except LLMProviderError:
            pass
        else:
            raise AssertionError("Expected LLMProviderError")

    with SessionLocal() as db:
        answer_log = db.query(AnswerLog).one()
        assert answer_log.answer_status == "provider_error"
        assert answer_log.error_message == "provider failed"


def test_answer_generation_excludes_superseded_source_versions():
    _, current_version_id = create_hmc_corpus_with_superseded_version()
    user = create_test_user()

    with SessionLocal() as db:
        result = generate_answer(
            db,
            user,
            "NYC Admin Code § 27-2005 obsolete owner standard",
            SearchFilters(),
            5,
        )
        cited_chunk = db.query(Chunk).filter_by(id=result.citations[0].chunk_id).one()

    assert result.answer_status == "answered"
    assert cited_chunk.source_version_id == current_version_id
    assert "current owner standard" in result.answer
    assert "obsolete owner standard" not in result.answer


class InvalidCitationProvider(AnswerProvider):
    provider_name = "fake"
    model_name = "invalid-citation-test"

    def generate(
        self,
        prompt: str,
        question: str,
        chunks: list[SearchResult],
    ) -> ProviderAnswer:
        return ProviderAnswer(
            answer_text="This answer cites a missing chunk.",
            cited_chunk_ids=["missing-chunk"],
            answer_status="answered",
        )


class FailingProvider(AnswerProvider):
    provider_name = "fake"
    model_name = "failure-test"

    def generate(
        self,
        prompt: str,
        question: str,
        chunks: list[SearchResult],
    ) -> ProviderAnswer:
        raise LLMProviderError("provider failed")
