from app.answer.providers import AnswerProvider, FakeAnswerProvider, LLMProviderError
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


def test_fake_provider_summarizes_hmc_heat_subdivisions():
    provider = FakeAnswerProvider("fake-answer-small")

    answer = provider.generate(
        "prompt",
        "What does the HMC say about heat and hot water?",
        [hmc_heat_result()],
    )

    assert answer.answer_status == "answered"
    assert answer.cited_chunk_ids == ["heat-chunk"]
    assert "October first through May thirty-first" in answer.answer_text
    assert "six a.m. and ten p.m." in answer.answer_text
    assert "sixty-eight degrees Fahrenheit" in answer.answer_text
    assert "below fifty-five degrees" in answer.answer_text
    assert "ten p.m. and six a.m." in answer.answer_text
    assert "sixty-two degrees Fahrenheit" in answer.answer_text
    assert "maintained a This response" not in answer.answer_text
    assert "and;" not in answer.answer_text


def test_fake_provider_summarizes_hpd_guidance_index_items():
    provider = FakeAnswerProvider("fake-answer-small")

    answer = provider.generate(
        "prompt",
        "What HPD enforcement information is available for tenants and owners?",
        [hpd_enforcement_result()],
    )

    assert answer.answer_status == "answered"
    assert answer.cited_chunk_ids == ["enforcement-chunk"]
    assert "About Code Enforcement" in answer.answer_text
    assert "Clear Violations" in answer.answer_text
    assert "eCertification" in answer.answer_text
    assert "HPD violations" in answer.answer_text


def test_fake_provider_still_refuses_unsupported_questions():
    provider = FakeAnswerProvider("fake-answer-small")

    answer = provider.generate(
        "prompt",
        "What did the court hold in an unpublished housing case?",
        [hmc_heat_result()],
    )

    assert answer.answer_status == "unsupported"
    assert answer.cited_chunk_ids == []
    assert "does not contain enough" in answer.answer_text


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


def hmc_heat_result() -> SearchResult:
    return SearchResult(
        chunk_id="heat-chunk",
        document_id="doc-1",
        source_id="source-1",
        source_version_id="version-1",
        source_name="NYC Housing Maintenance Code",
        source_type="law",
        jurisdiction="NYC",
        source_url="https://example.com/hmc",
        citation="NYC Admin Code § 27-2029",
        title="Minimum temperature to be maintained.",
        text=(
            "§ 27-2029 Minimum temperature to be maintained. "
            "a. During the period from October first through May thirty-first, "
            "centrally-supplied heat, in any dwelling in which such heat is "
            "required to be provided, shall be furnished so as to maintain, "
            "in every portion of such dwelling used or occupied for living "
            "purposes: (1) between the hours of six a.m. and ten p.m., a "
            "temperature of at least sixty-eight degrees Fahrenheit whenever "
            "the outside temperature falls below fifty-five degrees; and "
            "(2) between the hours of ten p.m. and six a.m., a temperature "
            "of at least sixty-two degrees Fahrenheit. b. During the period "
            "from October first through May thirty-first, all central heating "
            "systems required under this article shall be maintained free of "
            "any device which shall cause an otherwise operable central "
            "heating system to become incapable of providing the minimum "
            "requirements of heat or hot water."
        ),
        score=1.0,
        match_type="keyword",
    )


def hpd_enforcement_result() -> SearchResult:
    return SearchResult(
        chunk_id="enforcement-chunk",
        document_id="doc-1",
        source_id="source-1",
        source_version_id="version-1",
        source_name="HPD Tenant and Owner Guidance",
        source_type="guidance",
        jurisdiction="NYC",
        source_url="https://example.com/hpd/enforcement",
        citation=None,
        title="Enforcement",
        text=(
            "Enforcement About Code Enforcement Learn how code enforcement and "
            "the New York City Housing Maintenance Code. Clear Violations Learn "
            "how to clear housing code violations at your property. Correct "
            "Orders Learn about the different types of Orders HPD issues and how "
            "to correct them. eCertification Certify HPD violations and Housing "
            "Quality Standards failures online. Penalties and Fees Learn about "
            "civil penalties imposed by Housing Court."
        ),
        score=1.0,
        match_type="hybrid",
    )
