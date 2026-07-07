from app.db.session import SessionLocal
from app.models.source import Source
from app.retrieval.keyword import keyword_search
from app.retrieval.schemas import SearchFilters
from tests.retrieval_fixtures import create_retrieval_corpus


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
