from app.db.session import SessionLocal
from app.retrieval.embeddings import get_embedding_provider
from app.retrieval.schemas import SearchFilters
from app.retrieval.vector import vector_search
from tests.retrieval_fixtures import create_retrieval_corpus


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

