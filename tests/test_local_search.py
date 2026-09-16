from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.corpus.embeddings import EmbeddingProfile, LocalEmbeddingStore
from app.corpus.service import CorpusService, SourceArtifact
from app.retrieval.local import LocalSearch, LocalSearchFilters
from app.storage.database import LocalStorage
from app.storage.schema import chunks, generations
from app.workspace.paths import resolve_workspace_paths


@pytest.fixture
def local_search(tmp_path):
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=(
                    b"\xc2\xa7 711. Grounds for summary proceedings\n"
                    b"\xc2\xa7 713. Proceedings where no tenancy exists"
                ),
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    try:
        yield storage, LocalSearch(storage)
    finally:
        storage.close()


def test_exact_citation_and_keyword_search_share_active_generation(
    local_search,
) -> None:
    _storage, search = local_search
    response = search.search("RPAPL section 711", limit=5)

    assert response.method == "exact+keyword"
    assert response.results[0].citation == "RPAPL § 711"
    assert "exact" in response.results[0].match_types
    assert response.results[0].source_slug == "ny-rpapl"


def test_fts_query_treats_operator_like_input_as_text(local_search) -> None:
    _storage, search = local_search
    response = search.search('"proceedings" OR NOT (tenancy)', limit=5)

    assert response.results
    assert all(result.source_slug == "ny-rpapl" for result in response.results)


def test_source_filter_is_applied_before_limit(local_search) -> None:
    _storage, search = local_search
    response = search.search(
        "proceedings",
        filters=LocalSearchFilters(source_slug="missing-source"),
        limit=1,
    )
    assert response.results == ()


def test_profile_matched_vector_search_and_dimension_validation(local_search) -> None:
    storage, search = local_search
    with storage.corpus_engine.connect() as connection:
        chunk_rows = connection.execute(
            select(chunks.c.id, chunks.c.citation).order_by(chunks.c.citation)
        ).all()
    profile = EmbeddingProfile(
        id="fixture-two",
        provider="fake",
        model="fixture",
        dimension=2,
        preprocessing={"version": 1},
    )
    vector_store = LocalEmbeddingStore(storage)
    vector_store.put(chunk_rows[0].id, profile, [1.0, 0.0])
    vector_store.put(chunk_rows[1].id, profile, [0.0, 1.0])
    with storage.corpus_engine.begin() as connection:
        connection.execute(update(generations).values(profile_id=profile.id))

    response = search.search(
        "unrelated terms",
        query_vector=[0.9, 0.1],
        embedding_profile_id=profile.id,
    )
    assert response.semantic_status == "available"
    assert response.results[0].chunk_id == chunk_rows[0].id

    with pytest.raises(ValueError, match="dimension"):
        search.search(
            "query",
            query_vector=[1.0],
            embedding_profile_id=profile.id,
        )


def test_missing_vector_index_degrades_without_hiding_keyword_result(
    local_search,
) -> None:
    _storage, search = local_search
    response = search.search(
        "summary proceedings",
        query_vector=[1.0, 0.0],
        embedding_profile_id="not-installed",
    )

    assert response.semantic_status == "index_unavailable"
    assert response.results
    assert response.method == "exact+keyword"
