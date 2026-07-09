from app.db.session import SessionLocal
from app.retrieval.hybrid import hybrid_search
from app.retrieval.schemas import SearchFilters
from tests.retrieval_fixtures import (
    create_hmc_corpus_with_superseded_version,
    create_hmc_quality_corpus,
    create_hpd_guidance_quality_corpus,
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


def test_hybrid_search_prioritizes_broad_hmc_heat_query():
    create_hmc_quality_corpus()
    with SessionLocal() as db:
        results = hybrid_search(
            db,
            "What does the HMC say about heat and hot water?",
            SearchFilters(),
            5,
        )

    assert results[0].citation == "NYC Admin Code § 27-2029"
    assert "temperature" in results[0].text.lower()


def test_hybrid_search_prioritizes_owner_good_repair_query():
    create_hmc_quality_corpus()
    with SessionLocal() as db:
        results = hybrid_search(
            db,
            "What are an owner's good repair duties under the HMC?",
            SearchFilters(),
            5,
        )

    assert results[0].citation == "NYC Admin Code § 27-2005"
    assert "good repair" in results[0].text


def test_hybrid_search_returns_hpd_guidance_for_complaints_and_enforcement():
    create_hpd_guidance_quality_corpus()
    with SessionLocal() as db:
        complaint_results = hybrid_search(
            db,
            "How can a tenant report a housing complaint to HPD?",
            SearchFilters(source_type="guidance"),
            5,
        )
        enforcement_results = hybrid_search(
            db,
            "What HPD enforcement information is available?",
            SearchFilters(source_type="guidance"),
            5,
        )

    assert complaint_results[0].source_type == "guidance"
    assert complaint_results[0].title == "Report a Housing Complaint"
    assert complaint_results[0].source_url.endswith("report-a-housing-complaint.page")
    assert enforcement_results[0].source_type == "guidance"
    assert enforcement_results[0].title == "Enforcement"
    assert enforcement_results[0].source_url.endswith("enforcement.page")


def test_hybrid_search_excludes_superseded_source_versions():
    old_version_id, current_version_id = create_hmc_corpus_with_superseded_version()
    with SessionLocal() as db:
        results = hybrid_search(
            db,
            "NYC Admin Code § 27-2005 obsolete owner standard",
            SearchFilters(),
            5,
        )

    assert results
    assert {result.source_version_id for result in results} == {current_version_id}
    assert old_version_id not in {result.source_version_id for result in results}
    assert "obsolete owner standard" not in results[0].text


def test_unsupported_filter_keys_are_rejected():
    try:
        SearchFilters.from_dict({"sql": "drop table chunks"})
    except ValueError as exc:
        assert "Unsupported filters" in str(exc)
    else:
        raise AssertionError("Expected unsupported filter to raise ValueError")
