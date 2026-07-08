from app.db.session import SessionLocal
from app.retrieval.citations import citation_lookup
from app.retrieval.schemas import SearchFilters
from tests.retrieval_fixtures import (
    create_hmc_corpus_with_superseded_version,
    create_retrieval_corpus,
    create_rpapl_corpus_with_guidance_noise,
)


def test_exact_citation_lookup_finds_normalized_citation():
    create_retrieval_corpus()
    with SessionLocal() as db:
        results = citation_lookup(db, "NYC Admin Code § 27-2005", SearchFilters(), 5)

    assert results
    assert results[0].citation == "NYC Admin Code § 27-2005"
    assert results[0].source_url.startswith("https://")
    assert results[0].match_type == "citation"


def test_citation_lookup_finds_natural_rpapl_section_phrase():
    create_rpapl_corpus_with_guidance_noise()
    with SessionLocal() as db:
        results = citation_lookup(db, "RPAPL section 711", SearchFilters(), 5)

    assert results
    assert results[0].citation == "RPAPL § 711"
    assert results[0].match_type == "citation"


def test_citation_lookup_excludes_superseded_source_versions():
    old_version_id, current_version_id = create_hmc_corpus_with_superseded_version()
    with SessionLocal() as db:
        results = citation_lookup(db, "NYC Admin Code § 27-2005", SearchFilters(), 5)

    assert results
    assert {result.source_version_id for result in results} == {current_version_id}
    assert old_version_id not in {result.source_version_id for result in results}
    assert "obsolete owner standard" not in results[0].text
