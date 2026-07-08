from app.db.session import SessionLocal
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.schemas import SearchFilters
from app.retrieval.vector import vector_search
from tests.retrieval_fixtures import (
    create_hmc_corpus_with_superseded_version,
    create_retrieval_corpus,
)


def test_fake_embedding_provider_is_deterministic():
    provider = get_embedding_provider()

    assert provider.embed_text("heat required") == provider.embed_text("heat required")
    assert len(provider.embed_text("heat required")) == 16


def test_vector_search_returns_embedded_chunks():
    create_retrieval_corpus()
    with SessionLocal() as db:
        results = vector_search(db, "heat season", SearchFilters(), 5)

    assert results
    assert results[0].match_type == "vector"


def test_vector_search_excludes_superseded_source_versions():
    old_version_id, current_version_id = create_hmc_corpus_with_superseded_version()
    with SessionLocal() as db:
        results = vector_search(db, "obsolete owner standard", SearchFilters(), 5)

    assert results
    assert {result.source_version_id for result in results} == {current_version_id}
    assert old_version_id not in {result.source_version_id for result in results}
