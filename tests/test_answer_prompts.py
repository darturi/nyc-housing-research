from app.answer.prompts import (
    LEGAL_INFORMATION_DISCLAIMER,
    PERSONAL_SCENARIO_INSTRUCTION,
    build_prompt,
    is_personal_housing_scenario,
    trim_context,
)
from app.answer.schemas import PromptContext
from app.retrieval.schemas import SearchResult


def test_build_prompt_includes_rules_question_chunks_and_disclaimer():
    result = _result("chunk-1")

    prompt = build_prompt(
        PromptContext(
            question="What does section 27-2005 require?",
            chunks=[result],
            source_coverage="Coverage statement",
            disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        )
    )

    assert "What does section 27-2005 require?" in prompt
    assert "chunk_id: chunk-1" in prompt
    assert "NYC Admin Code § 27-2005" in prompt
    assert "https://example.test/source" in prompt
    assert "Put citations only in cited_chunk_ids" in prompt
    assert "Coverage statement" in prompt
    assert LEGAL_INFORMATION_DISCLAIMER in prompt


def test_trim_context_limits_chunks_and_characters():
    results = [
        _result("chunk-1", text="a" * 10),
        _result("chunk-2", text="b" * 10),
    ]

    trimmed = trim_context(results, max_chunks=2, max_chars=12)

    assert [result.chunk_id for result in trimmed] == ["chunk-1", "chunk-2"]
    assert trimmed[0].text == "a" * 10
    assert trimmed[1].text == "bb"


def test_personal_eviction_prompt_prohibits_outcome_prediction():
    prompt = build_prompt(
        PromptContext(
            question="Will I win my nonpayment eviction case?",
            chunks=[_result("chunk-1")],
            source_coverage=None,
            disclaimer=LEGAL_INFORMATION_DISCLAIMER,
        )
    )

    assert is_personal_housing_scenario("Will I win my nonpayment eviction case?")
    assert PERSONAL_SCENARIO_INSTRUCTION in prompt


def _result(chunk_id: str, text: str | None = None) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id="document-id",
        source_id="source-id",
        source_version_id="source-version-id",
        source_name="NYC Housing Maintenance Code",
        source_type="law",
        jurisdiction="NYC",
        source_url="https://example.test/source",
        citation="NYC Admin Code § 27-2005",
        title="Duties of owner",
        text=text or "The owner shall keep the premises in good repair.",
        score=1.0,
        match_type="citation",
    )
