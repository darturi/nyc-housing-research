from app.retrieval.query_quality import (
    expand_query_terms,
    focused_query_texts,
    rerank_results,
)
from app.retrieval.schemas import SearchResult


def test_heat_query_expands_to_temperature_terms():
    terms = expand_query_terms("What does the HMC say about heat and hot water?")

    assert "heat" in terms
    assert "temperature" in terms
    assert "minimum" in terms


def test_heat_query_adds_focused_keyword_queries():
    queries = focused_query_texts("What does the HMC say about heat and hot water?")

    assert "temperature" in queries
    assert "minimum temperature" in queries


def test_owner_repair_query_expands_to_duties_terms():
    terms = expand_query_terms("What are an owner's good repair duties?")

    assert "owner" in terms
    assert "duties" in terms
    assert "repair" in terms
    assert "premises" in terms


def test_hpd_complaint_and_enforcement_queries_expand():
    complaint_terms = expand_query_terms("How can I report a housing complaint?")
    enforcement_terms = expand_query_terms("What does HPD enforcement involve?")

    assert "311" in complaint_terms
    assert "inspection" in complaint_terms
    assert "enforcement" in enforcement_terms
    assert "violation" in enforcement_terms


def test_eviction_and_certification_queries_get_focused_statutory_guidance_terms():
    terms = expand_query_terms("Does a 14-day rent demand apply to nonpayment?")
    focused = focused_query_texts("How do I use eCertification to clear violations?")

    assert "nonpayment" in terms
    assert "RPAPL" in terms
    assert "eCertification" in focused
    assert "clear violations" in focused


def test_rerank_demotes_pure_vector_noise_below_lexical_match():
    results = [
        search_result(
            chunk_id="noise",
            citation="NYC Admin Code § 27-2047",
            title="Mail service",
            text="Owners arrange mail delivery.",
            score=5.0,
            match_type="vector",
        ),
        search_result(
            chunk_id="match",
            citation="NYC Admin Code § 27-2029",
            title="Minimum temperature to be maintained",
            text="Owners maintain minimum temperature when heat is required.",
            score=1.0,
            match_type="keyword",
        ),
    ]

    reranked = rerank_results(results, "What does the HMC say about heat?")

    assert reranked[0].chunk_id == "match"


def test_rerank_preserves_exact_citation_priority():
    results = [
        search_result(
            chunk_id="keyword",
            citation="NYC Admin Code § 27-2029",
            title="Minimum temperature to be maintained",
            text="Owners maintain minimum temperature when heat is required.",
            score=20.0,
            match_type="keyword",
        ),
        search_result(
            chunk_id="citation",
            citation="NYC Admin Code § 27-2005",
            title="Duties of owner",
            text="Owners keep premises in good repair.",
            score=1.0,
            match_type="citation",
        ),
    ]

    reranked = rerank_results(results, "NYC Admin Code § 27-2005")

    assert reranked[0].chunk_id == "citation"


def search_result(
    *,
    chunk_id: str,
    citation: str | None,
    title: str,
    text: str,
    score: float,
    match_type: str,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        document_id="document",
        source_id="source",
        source_version_id="source-version",
        source_name="NYC Housing Maintenance Code",
        source_type="law",
        jurisdiction="NYC",
        source_url="https://example.com",
        citation=citation,
        title=title,
        text=text,
        score=score,
        match_type=match_type,
    )
