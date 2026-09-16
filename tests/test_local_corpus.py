from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text

from app.corpus.service import CorpusService, CorpusValidationError, SourceArtifact
from app.storage.database import LocalStorage
from app.storage.schema import corpus_state, generations, source_versions
from app.workspace.paths import resolve_workspace_paths


@pytest.fixture
def corpus(tmp_path):
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    try:
        yield storage, CorpusService(storage)
    finally:
        storage.close()


def rpapl_artifact(text_value: str, *, days: int = 0) -> SourceArtifact:
    return SourceArtifact(
        slug="ny-rpapl",
        content=text_value.encode(),
        source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
        content_type="text/plain",
        retrieved_at=datetime(2026, 9, 14, tzinfo=UTC) + timedelta(days=days),
    )


def test_partial_text_generation_is_deliberate_searchable_and_traceable(corpus) -> None:
    storage, service = corpus
    generation = service.install_artifacts(
        [rpapl_artifact("§ 711. Grounds where landlord-tenant relationship exists")],
        allow_partial=True,
    )

    status = service.status()
    assert status.active_generation_id == generation
    assert status.readiness == "partial_text_ready"
    assert status.source_count == 1
    assert status.chunk_count == 1
    assert status.embedding_ready_count == 0
    verification = service.verify()
    assert verification["generation_id"] == generation
    assert verification["verified_sources"] == ["ny-rpapl"]
    assert verification["chunk_count"] == verification["fts_count"] == 1
    assert verification["citation_invariants"] == "verified"
    with storage.corpus_engine.connect() as connection:
        matched = connection.scalar(
            text(
                "SELECT chunk_id FROM chunk_fts "
                "WHERE generation_id = :generation AND chunk_fts MATCH :query"
            ),
            {"generation": generation, "query": "landlord tenant"},
        )
    assert matched is not None


def test_failed_validation_preserves_active_generation(corpus) -> None:
    _storage, service = corpus
    active = service.install_artifacts(
        [rpapl_artifact("§ 711. Required baseline provision")],
        allow_partial=True,
    )

    with pytest.raises(CorpusValidationError, match="missing required citation"):
        service.install_artifacts(
            [rpapl_artifact("§ 999. Wrong source content", days=1)],
            allow_partial=True,
        )

    assert service.status().active_generation_id == active


def test_artifact_publication_failure_preserves_active_generation(
    corpus, monkeypatch
) -> None:
    storage, service = corpus
    active = service.install_artifacts(
        [rpapl_artifact("§ 711. Required baseline provision")],
        allow_partial=True,
    )

    def fail_publication(_source, _destination):
        raise OSError("simulated disk-full publication failure")

    monkeypatch.setattr("app.corpus.service.os.replace", fail_publication)
    with pytest.raises(OSError, match="disk-full"):
        service.install_artifacts(
            [rpapl_artifact("§ 711. Revised but uncommitted provision", days=1)],
            allow_partial=True,
        )

    assert service.status().active_generation_id == active
    assert len(list(storage.paths.artifacts.rglob("*.*"))) == 1


def test_update_switches_atomically_and_rollback_restores_previous(corpus) -> None:
    storage, service = corpus
    first = service.install_artifacts(
        [rpapl_artifact("§ 711. Original text")], allow_partial=True
    )
    second = service.install_artifacts(
        [rpapl_artifact("§ 711. Revised text\n§ 713. Additional text", days=1)],
        allow_partial=True,
    )

    assert second != first
    assert service.status().chunk_count == 2
    with storage.corpus_engine.connect() as connection:
        first_state = connection.scalar(
            select(generations.c.status).where(generations.c.id == first)
        )
        assert first_state == "retained"

    restored = service.rollback()
    assert restored == first
    with storage.corpus_engine.connect() as connection:
        assert (
            connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            == first
        )
    assert service.status().chunk_count == 1

    verification = service.verify_all_retained()
    assert verification["status"] == "verified"
    assert verification["generation_count"] == 2
    assert verification["verified_source_references"] == 2
    assert {item["generation_id"] for item in verification["generations"]} == {
        first,
        second,
    }


def test_complete_core_activation_rejects_missing_modules(corpus) -> None:
    storage, service = corpus
    with pytest.raises(CorpusValidationError, match="missing source"):
        service.install_artifacts(
            [rpapl_artifact("§ 711. Valid but incomplete core")],
            allow_partial=False,
        )
    assert service.status().active_generation_id is None
    assert list(storage.paths.artifacts.rglob("*.*")) == []


def test_semantically_unchanged_source_reuses_version_and_generation(corpus) -> None:
    storage, service = corpus
    first = service.install_artifacts(
        [rpapl_artifact("§ 711. Grounds where landlord-tenant relationship exists")],
        allow_partial=True,
    )
    second = service.install_artifacts(
        [
            rpapl_artifact(
                "§ 711.  Grounds where landlord-tenant relationship exists",
                days=1,
            )
        ],
        allow_partial=True,
    )

    assert second == first
    with storage.corpus_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(source_versions)) == 1
    assert len(list(storage.paths.artifacts.rglob("*.*"))) == 1
