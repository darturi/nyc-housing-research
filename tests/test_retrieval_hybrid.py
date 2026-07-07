from app.db.session import SessionLocal
from app.retrieval.hybrid import hybrid_search
from app.retrieval.schemas import SearchFilters
from tests.retrieval_fixtures import (
    create_retrieval_corpus,
    create_rpapl_corpus_with_guidance_noise,
)


def test_hybrid_search_prioritizes_exact_citation_and_deduplicates():
    create_retrieval_corpus()
    with SessionLocal() as db:
        results = hybrid_search(
            db,
            "NYC Admin Code § 27-2005 good repair",
            SearchFilters(),
            10,
        )

    chunk_ids = [result.chunk_id for result in results]
    assert len(chunk_ids) == len(set(chunk_ids))
    assert results[0].citation == "NYC Admin Code § 27-2005"
    assert results[0].match_type == "citation"


def test_hybrid_search_prioritizes_natural_rpapl_section_phrase():
    create_rpapl_corpus_with_guidance_noise()
    with SessionLocal() as db:
        results = hybrid_search(
            db,
            "What does RPAPL section 711 cover?",
            SearchFilters(),
            5,
        )

    assert results[0].citation == "RPAPL § 711"
    assert results[0].match_type == "citation"
    assert "BOOMR" not in results[0].text


def test_unsupported_filter_keys_are_rejected():
    try:
        SearchFilters.from_dict({"sql": "drop table chunks"})
    except ValueError as exc:
        assert "Unsupported filters" in str(exc)
    else:
        raise AssertionError("Expected unsupported filter to raise ValueError")
