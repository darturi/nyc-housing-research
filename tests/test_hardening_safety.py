import asyncio
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.answer.local import LocalAnswerService
from app.cli.main import main
from app.corpus.indexing import CorpusEmbeddingIndexer
from app.corpus.resources import ResourceMetadata, ResourceService
from app.corpus.service import CorpusService, SourceArtifact
from app.credentials.store import CredentialResolver
from app.hpd.connector import PropertyQuery, PropertySearchResponse
from app.jobs.service import JobService, JobState
from app.local_app import create_local_app
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import get_profile
from app.retrieval.local import LocalSearchFilters
from app.storage.database import LocalStorage
from app.storage.schema import usage_events
from app.usage.ledger import PaidCapacityUnavailable, SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied


@pytest.fixture
def workspace(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content="§ 711. Grounds for summary proceedings.".encode(),
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime.now(UTC),
            )
        ],
        allow_partial=True,
    )
    yield context, storage
    storage.close()


def authenticate(client, app):
    token = client.post(
        "/api/v1/session/exchange",
        json={
            "launch_token": app.state.launch_token,
        },
        headers={"Origin": "http://127.0.0.1"},
    ).json()["csrf_token"]
    return {"Origin": "http://127.0.0.1", "X-CSRF-Token": token}


def empty_property(building="111"):
    return PropertySearchResponse(
        query=PropertyQuery(building_id=building),
        candidates=(),
        records=(),
        requires_selection=False,
        continuation=None,
        is_complete=True,
        returned_count=0,
        fetched_at=datetime.now(UTC),
        dataset_id="wvxf-dwi5",
        dataset_url="https://data.cityofnewyork.us/",
        connector_version="fixture",
        source_status="verified_zero",
        total_count=0,
    )


@pytest.mark.parametrize("revoke_in_callback", [False, True])
def test_revoked_consent_never_reaches_answer_gateway(
    workspace, monkeypatch, revoke_in_callback
):
    context, storage = workspace
    resources = ResourceService(storage)
    private = resources.add(
        b"Private: a distinctive purple radiator.",
        filename="private.txt",
        metadata=ResourceMetadata(title="Private", model_use_allowed=True),
    )
    resources.add(
        b"Public radiator maintenance guide.",
        filename="public.txt",
        metadata=ResourceMetadata(title="Public", model_use_allowed=True),
    )
    gateway = ProviderGateway(context, UsageLedger(storage))
    prompts = []
    original = gateway.answer

    def capture(**kwargs):
        prompts.append(kwargs["prompt"])
        return original(**kwargs)

    monkeypatch.setattr(gateway, "answer", capture)
    try:
        CorpusEmbeddingIndexer(storage, gateway).index_active(
            get_profile("fake-small-16"), credential=None, approve_cost=False
        )
        service = LocalAnswerService(
            context, storage, gateway, CredentialResolver(context, environment={})
        )
        filters = LocalSearchFilters(origin="user")
        service.answer("What color was the radiator?", filters=filters)
        assert "distinctive purple" in prompts[-1]
        callback = None
        if revoke_in_callback:

            def callback(*_):
                resources.set_model_use(private.resource_id, False)
        else:
            resources.set_model_use(private.resource_id, False)
            CorpusService(storage).rollback()
        result = service.answer(
            "What color was the radiator?", filters=filters, on_evidence=callback
        )
        assert result.status == "synthetic_demo"
        assert "distinctive purple" not in prompts[-1]
        assert all(item.source_slug != private.slug for item in result.evidence)
    finally:
        gateway.close()


def test_saved_policy_applies_to_new_workers_and_existing_gateway(
    workspace, monkeypatch
):
    context, _storage = workspace
    app = create_local_app(context)
    captured = []
    done = threading.Event()

    def capture(service, *args, **kwargs):
        captured.append(service._context)
        done.set()
        raise ValueError("No provider request in this fixture")

    monkeypatch.setattr(LocalAnswerService, "answer", capture)
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as transport:
        gateway = ProviderGateway(
            app.state.workspace, UsageLedger(app.state.storage), client=transport
        )
        with TestClient(app, base_url="http://127.0.0.1") as client:
            headers = authenticate(client, app)
            response = client.patch(
                "/api/v1/settings",
                headers=headers,
                json={
                    "offline": True,
                    "monthly_budget_usd": "0",
                    "answer_deadline_seconds": 7,
                },
            )
            assert response.status_code == 200
            assert response.json()["restart_required_for_active_jobs"] is False
            assert (
                client.post(
                    "/api/v1/query", headers=headers, json={"question": "RPAPL 711"}
                ).status_code
                == 202
            )
            assert done.wait(3)
            assert captured[0].network.offline is True
            assert captured[0].settings.monthly_budget_usd == "0"
            assert captured[0].settings.answer_deadline_seconds == 7
            with pytest.raises(NetworkAccessDenied):
                gateway.answer(
                    prompt="test",
                    profile=get_profile("openai-answer-luna-v1"),
                    credential="fixture",
                )
            assert calls == []


def test_two_tabs_keep_valid_csrf_tokens(workspace):
    context, _storage = workspace
    app = create_local_app(context)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        first = authenticate(client, app)
        second_token = client.get("/api/v1/session/csrf").json()["csrf_token"]
        for headers in (first, first | {"X-CSRF-Token": second_token}):
            response = client.patch(
                "/api/v1/settings", headers=headers, json={"reading_style": "plain"}
            )
            assert response.status_code == 200


def test_dense_input_is_reserved_before_network_and_input_limits_are_enforced(
    workspace,
):
    context, storage = workspace
    context = replace(
        context,
        settings=replace(
            context.settings,
            monthly_budget_usd="0.00146",
            per_operation_budget_usd="0.00146",
        ),
    )
    calls = []
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: calls.append(request))
    ) as client:
        gateway = ProviderGateway(context, UsageLedger(storage), client=client)
        with pytest.raises(SpendDenied):
            gateway.answer(
                prompt="q" * 400,
                profile=get_profile("openai-answer-luna-v1"),
                credential="fixture",
            )
        with pytest.raises(ProviderExecutionError, match="token limit"):
            gateway.embeddings(
                inputs=["界" * 3000],
                profile=get_profile("openai-embedding-3-small-v1"),
                credential="fixture",
            )
    assert calls == []
    with storage.state_engine.connect() as connection:
        assert connection.scalar(select(usage_events.c.id)) is None


@pytest.mark.parametrize("cap_at_call", [False, True])
def test_approved_operation_ceiling_includes_prior_batches(workspace, cap_at_call):
    context, storage = workspace
    profile = replace(
        get_profile("openai-embedding-3-small-v1"),
        dimension=2,
        input_usd_per_million=Decimal("1"),
    )
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [1, 0]}],
                "usage": {"prompt_tokens": 10},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for batch in range(2):
            gateway = ProviderGateway(
                context,
                UsageLedger(storage),
                client=client,
                operation_cap_usd=None if cap_at_call else Decimal("0.000015"),
            )
            if batch == 0:
                gateway.embeddings(
                    inputs=["x" * 10],
                    profile=profile,
                    credential="fixture",
                    operation_id="resumed-index",
                    operation_cap_usd=Decimal("0.000015") if cap_at_call else None,
                )
            else:
                with pytest.raises(SpendDenied, match="operation"):
                    gateway.embeddings(
                        inputs=["x" * 10],
                        profile=profile,
                        credential="fixture",
                        operation_id="resumed-index",
                        operation_cap_usd=Decimal("0.000015") if cap_at_call else None,
                    )
    assert len(calls) == 1


def test_restart_recovers_dead_owners_without_waiting_for_lease(workspace):
    context, storage = workspace
    code = """
import os, sys
from decimal import Decimal
from app.workspace.context import WorkspaceContext
from app.storage.database import LocalStorage
from app.jobs.service import JobService
from app.usage.ledger import UsageLedger
context = WorkspaceContext.from_options(sys.argv[1], environment={})
storage = LocalStorage.open(context.paths)
jobs = JobService(storage.state_engine)
job = jobs.create("corpus_install", "dead-running")
jobs.claim(job.id, "worker", lease_seconds=300)
jobs.create("answer", "dead-queued")
UsageLedger(storage).reserve(
    operation_id="dead", attempt_id="dead-attempt", provider="fixture",
    profile_id="fixture", projected_usd=Decimal("1"),
    monthly_cap_usd=Decimal("15"), per_operation_cap_usd=Decimal("2"),
    timezone="America/New_York", price_snapshot={},
)
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", code, str(context.paths.root)],
        check=True,
        cwd=Path(__file__).parents[1],
    )
    jobs = JobService(storage.state_engine)
    assert jobs.recover_interrupted() == 2
    states = {job.target_id: job.state for job in jobs.list()}
    assert states["dead-running"] == JobState.PAUSED
    assert states["dead-queued"] == JobState.FAILED
    ledger = UsageLedger(storage)
    summary = ledger.summary(monthly_cap_usd=Decimal("15"), timezone="America/New_York")
    assert summary.reserved_usd == 0
    assert summary.uncertain_usd == 1
    assert ledger.unresolved_attempts()[0]["attempt_id"] == "dead-attempt"
    ledger.correct_uncertain(
        "dead-attempt", actual_usd=Decimal("0.25"), reason="Verified provider receipt"
    )
    assert ledger.summary(
        monthly_cap_usd=Decimal("15"), timezone="America/New_York"
    ).settled_usd == Decimal("0.25")
    live = jobs.create("corpus_update", "live")
    jobs.claim(live.id, "live-worker", lease_seconds=300)
    jobs.create("answer", "live-queued")
    assert JobService(storage.state_engine).recover_interrupted() == 0


def test_timezone_validation_without_system_timezone_files():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.workspace.settings import LocalSettings; "
            "LocalSettings(workspace_id='timezone-test').validate()",
        ],
        check=True,
        env={**os.environ, "PYTHONTZPATH": "", "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_live_provider_keeps_capacity_and_blocks_maintenance_after_lease_expiry(
    workspace,
):
    from app.maintenance.backup import BackupError, WorkspaceBackupService

    _context, storage = workspace
    ledger = UsageLedger(storage)
    now = datetime.now(UTC)
    kwargs = dict(
        operation_id="live",
        provider="fixture",
        profile_id="fixture",
        projected_usd=Decimal("1"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("3"),
        timezone="America/New_York",
        price_snapshot={},
        max_concurrent=1,
    )
    ledger.reserve(attempt_id="live-call", now=now, lease_seconds=1, **kwargs)
    with pytest.raises(PaidCapacityUnavailable):
        ledger.reserve(attempt_id="blocked", now=now + timedelta(seconds=2), **kwargs)
    with pytest.raises(BackupError, match="provider calls"):
        WorkspaceBackupService(storage)._enter_barrier(now + timedelta(seconds=2))


def test_usage_cli_lists_and_reconciles_uncertain_attempts(workspace, capsys):
    import json

    context, storage = workspace
    ledger = UsageLedger(storage)
    reservation = ledger.reserve(
        operation_id="review",
        attempt_id="review-attempt",
        provider="fixture",
        profile_id="fixture",
        projected_usd=Decimal("1"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={},
    )
    ledger.mark_uncertain(
        reservation, price_snapshot={}, provider="fixture", profile_id="fixture"
    )
    base = ["--data-dir", str(context.paths.root), "usage", "--json", "--attempts"]
    assert main(base) == 0
    assert json.loads(capsys.readouterr().out)["attempts"][0]["attempt_id"] == (
        "review-attempt"
    )
    assert (
        main(
            base
            + [
                "--reconcile",
                "review-attempt",
                "--actual-usd",
                "0.25",
                "--reason",
                "Verified provider receipt",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["attempts"] == []
    assert Decimal(payload["settled_usd"]) == Decimal("0.25")


def test_property_network_wait_does_not_block_other_routes(workspace, monkeypatch):
    context, _storage = workspace
    app = create_local_app(context)
    started = threading.Event()
    release = threading.Event()

    def search(*args, **kwargs):
        started.set()
        release.wait(2)
        return empty_property()

    monkeypatch.setattr(
        "app.local_app._property_repository",
        lambda *args: (
            SimpleNamespace(close=lambda: None),
            SimpleNamespace(search=search),
        ),
    )

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client:
            response = await client.post(
                "/api/v1/session/exchange",
                headers={"Origin": "http://127.0.0.1"},
                json={"launch_token": app.state.launch_token},
            )
            headers = {
                "Origin": "http://127.0.0.1",
                "X-CSRF-Token": response.json()["csrf_token"],
            }
            before = time.monotonic()
            lookup = asyncio.create_task(
                client.post(
                    "/api/v1/properties/search",
                    headers=headers,
                    json={"building_id": "111"},
                )
            )
            try:
                assert await asyncio.to_thread(started.wait, 2)
                settings = await asyncio.wait_for(client.get("/api/v1/settings"), 1)
                assert settings.status_code == 200
                assert time.monotonic() - before < 1.5
            finally:
                release.set()
            assert (await lookup).status_code == 200

    with TestClient(app):
        asyncio.run(exercise())


def test_fts_is_shared_across_generations_and_history_remains_searchable(workspace):
    from app.retrieval.local import LocalSearch

    _context, storage = workspace
    service = ResourceService(storage)
    resource = service.add(
        b"Private radiator guide.",
        filename="guide.txt",
        metadata=ResourceMetadata(title="Guide"),
    )
    original = CorpusService(storage).status().active_generation_id
    for allowed in (True, False, True, False):
        service.set_model_use(resource.resource_id, allowed)
    with storage.corpus_engine.connect() as connection:
        fts = connection.scalar(text("SELECT count(*) FROM chunk_fts"))
        unique = connection.scalar(
            text("SELECT count(DISTINCT chunk_id) FROM generation_chunks")
        )
        assert fts == unique
    assert (
        LocalSearch(storage)
        .search(
            "radiator",
            filters=LocalSearchFilters(origin="user"),
            generation_id=original,
        )
        .results
    )
