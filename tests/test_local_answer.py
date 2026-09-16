import time
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.answer.local import LocalAnswerService
from app.corpus.indexing import CorpusEmbeddingIndexer
from app.corpus.service import CorpusService, CorpusValidationError, SourceArtifact
from app.credentials.store import CredentialResolver
from app.jobs.interactive import InteractiveAnswerJobs
from app.jobs.service import JobService
from app.providers.gateway import ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    get_configured_profile,
    get_profile,
)
from app.storage.database import LocalStorage
from app.storage.schema import corpus_state, generations, usage_events
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.settings import save_local_settings


def _workspace(tmp_path, *, settings=None, environment=None):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    if settings:
        context = replace(context, settings=replace(context.settings, **settings))
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=(
                    b"\xc2\xa7 711. A landlord may maintain a summary proceeding "
                    b"on the grounds stated in this section."
                ),
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    return context, storage, CredentialResolver(context, environment=environment or {})


def test_fake_indexing_stages_then_atomically_activates_hybrid_generation(
    tmp_path,
) -> None:
    context, storage, _credentials = _workspace(tmp_path)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        old_active = CorpusService(storage).status().active_generation_id
        result = CorpusEmbeddingIndexer(storage, gateway).index_active(
            get_profile("fake-small-16"),
            credential=None,
            approve_cost=False,
            batch_size=1,
        )
        assert result.generation_id != old_active
        with storage.corpus_engine.connect() as connection:
            active = connection.scalar(
                select(corpus_state.c.active_generation_id).where(
                    corpus_state.c.id == 1
                )
            )
            generation = (
                connection.execute(
                    select(generations).where(generations.c.id == active)
                )
                .mappings()
                .one()
            )
        assert active == result.generation_id
        assert generation["profile_id"] == "fake-small-16"
        assert generation["readiness"] == "hybrid_ready"
        CorpusService(storage).rollback()
        CorpusService(storage).activate(result.generation_id, allow_partial=True)
        assert (
            CorpusService(storage).status().active_generation_id == result.generation_id
        )
    finally:
        gateway.close()
        storage.close()


def test_indexing_estimate_is_read_only_and_reports_reuse(tmp_path) -> None:
    context, storage, _credentials = _workspace(tmp_path)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        indexer = CorpusEmbeddingIndexer(storage, gateway)
        before = CorpusService(storage).status().active_generation_id
        estimate = indexer.estimate_active(get_profile("fake-small-16"))
        after = CorpusService(storage).status().active_generation_id
        assert estimate.total_chunks == 1
        assert estimate.chunks_requiring_embedding == 1
        assert estimate.estimated_input_tokens > 0
        assert estimate.estimated_cost_usd == 0
        assert before == after
    finally:
        gateway.close()
        storage.close()


def test_unknown_price_embedding_profile_cannot_enter_batch_indexing(tmp_path) -> None:
    context, storage, _credentials = _workspace(tmp_path)
    settings = replace(
        context.settings,
        embedding_profile="openai-embedding-3-small-v1",
        profile_overrides={
            "openai-embedding-3-small-v1": {
                "endpoint": "https://gateway.example/v1/embeddings",
                "auth_slot": "unknown-embedding",
                "input_usd_per_million": None,
                "output_usd_per_million": None,
                "price_effective_date": None,
                "price_source": None,
                "stores_response": False,
            }
        },
    ).validate()
    save_local_settings(context.paths, settings)
    profile = get_configured_profile(
        settings, ProfileKind.EMBEDDING, require_compatibility=False
    )
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        with pytest.raises(CorpusValidationError, match="no verified price"):
            CorpusEmbeddingIndexer(storage, gateway).estimate_active(profile)
    finally:
        gateway.close()
        storage.close()


def test_query_embedding_and_answer_share_one_metered_operation(tmp_path) -> None:
    context, storage, credentials = _workspace(tmp_path)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        CorpusEmbeddingIndexer(storage, gateway).index_active(
            get_profile("fake-small-16"),
            credential=None,
            approve_cost=False,
        )
        operation_id = "answer-job-fixture"
        result = LocalAnswerService(
            context,
            storage,
            gateway,
            credentials,
        ).answer(
            "What does RPAPL section 711 concern?",
            operation_id=operation_id,
        )
        assert result.status == "synthetic_demo"
        with storage.state_engine.connect() as connection:
            rows = list(
                connection.execute(
                    select(
                        usage_events.c.attempt_id,
                        usage_events.c.event_type,
                    ).where(usage_events.c.operation_id == operation_id)
                )
            )
        assert len(rows) == 4
        assert len({row.attempt_id for row in rows}) == 2
        assert {row.event_type for row in rows} == {"reserve", "settle"}
    finally:
        gateway.close()
        storage.close()


def test_expired_interactive_result_requires_explicit_resubmission(tmp_path) -> None:
    context, storage, _credentials = _workspace(tmp_path)
    jobs = InteractiveAnswerJobs(context, storage, expiry_minutes=-1)
    try:
        job_id = jobs.submit("What does RPAPL section 711 concern?")
        state = None
        for _ in range(100):
            state = JobService(storage.state_engine).get(job_id).state.value
            if state in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert state == "succeeded"
        expired = jobs.get(job_id)
        assert expired["status"] == "expired_result"
        assert expired["result"] is None
        assert expired["resubmit_required"] is True
        assert expired["warnings"]
    finally:
        jobs.close()
        storage.close()


def test_grounded_answer_returns_only_supplied_evidence_without_persistence(
    tmp_path,
) -> None:
    context, storage, credentials = _workspace(tmp_path)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        result = LocalAnswerService(context, storage, gateway, credentials).answer(
            "What does RPAPL section 711 concern?"
        )
        assert result.status == "synthetic_demo"
        assert "Synthetic provider output" in result.answer
        assert "[E1]" in result.answer
        assert result.evidence[0].citation == "RPAPL § 711"
        assert result.question not in storage.paths.state_database.read_bytes().decode(
            "latin-1"
        )
    finally:
        gateway.close()
        storage.close()


def test_provider_failure_preserves_retrieved_sources(tmp_path) -> None:
    context, storage, credentials = _workspace(
        tmp_path,
        settings={"answer_profile": "openai-answer-luna-v1"},
        environment={"NYC_HOUSING_OPENAI_API_KEY": "sk-fixture-example-key"},
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "fixture"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        result = LocalAnswerService(context, storage, gateway, credentials).answer(
            "What does RPAPL section 711 concern?"
        )
        assert result.status == "provider_error"
        assert result.evidence
        assert "remain available" in result.answer
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_historical_question_is_not_answered_from_undated_current_sources(
    tmp_path,
) -> None:
    context, storage, credentials = _workspace(tmp_path)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        result = LocalAnswerService(context, storage, gateway, credentials).answer(
            "What did RPAPL section 711 require as of 2020?"
        )
        assert result.status == "historical_coverage_unavailable"
        assert result.evidence
        assert "historical reconstruction" in result.answer
        assert result.cost_usd == "0"
    finally:
        gateway.close()
        storage.close()


def test_query_embedding_failure_falls_back_to_keyword_evidence(
    tmp_path, monkeypatch
) -> None:
    context, storage, credentials = _workspace(
        tmp_path,
        settings={"embedding_profile": "openai-embedding-3-small-v1"},
        environment={"NYC_HOUSING_OPENAI_API_KEY": "sk-fixture-example-key"},
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "fixture"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    service = LocalAnswerService(context, storage, gateway, credentials)
    monkeypatch.setattr(service, "_semantic_ready", lambda _profile: True)
    try:
        result = service.answer("What does RPAPL section 711 concern?")
        assert result.status == "synthetic_demo"
        assert result.evidence
        assert result.retrieval_method == "exact+keyword"
        assert result.semantic_status == "fallback_embedding_failed"
    finally:
        gateway.close()
        client.close()
        storage.close()
