from app.db.session import SessionLocal
from app.models.source import Source
from app.retrieval.common import filter_sql
from app.retrieval.keyword import keyword_search
from app.retrieval.schemas import SearchFilters
from tests.retrieval_fixtures import (
    create_hmc_corpus_with_superseded_version,
    create_retrieval_corpus,
)


def test_keyword_search_finds_chunk_text_and_applies_filters():
    create_retrieval_corpus()
    with SessionLocal() as db:
        results = keyword_search(
            db,
            "good repair",
            SearchFilters(jurisdiction="NYC"),
            5,
        )
        filtered = keyword_search(
            db,
            "good repair",
            SearchFilters(jurisdiction="NY"),
            5,
        )

    assert results
    assert "good repair" in results[0].text
    assert filtered == []


def test_keyword_search_excludes_ineligible_sources():
    create_retrieval_corpus()
    with SessionLocal() as db:
        source = db.query(Source).filter_by(slug="nyc-housing-maintenance-code").one()
        source.license_status = "restricted"
        db.commit()

        results = keyword_search(db, "good repair", SearchFilters(), 5)

    assert results == []


def test_keyword_search_excludes_superseded_source_versions():
    old_version_id, current_version_id = create_hmc_corpus_with_superseded_version()
    with SessionLocal() as db:
        obsolete_results = keyword_search(
            db,
            "obsolete owner standard",
            SearchFilters(),
            5,
        )
        current_results = keyword_search(
            db,
            "current owner standard",
            SearchFilters(),
            5,
        )

    assert {result.source_version_id for result in obsolete_results} == {
        current_version_id,
    }
    assert old_version_id not in {
        result.source_version_id for result in obsolete_results
    }
    assert "obsolete owner standard" not in obsolete_results[0].text
    assert current_results
    assert {result.source_version_id for result in current_results} == {
        current_version_id,
    }
    assert old_version_id not in {
        result.source_version_id for result in current_results
    }


def test_raw_sql_filters_require_current_source_versions():
    where_sql, _ = filter_sql(SearchFilters())

    assert "sv.is_current = true" in where_sql
