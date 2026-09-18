import time
from dataclasses import replace
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.corpus.service import CorpusService, SourceArtifact
from app.local_app import create_local_app
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext
from app.workspace.settings import save_local_settings


def _prepared_app(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings.",
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    storage.close()
    return context, create_local_app(context)


def _authenticate(client, app):
    response = client.post(
        "/api/v1/session/exchange",
        headers={"Origin": "http://127.0.0.1"},
        json={"launch_token": app.state.launch_token},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _mutation_headers(csrf):
    return {"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf}


def _wait_job(client, job_id):
    result = None
    for _ in range(200):
        result = client.get(f"/api/v1/jobs/{job_id}").json()
        if result["state"] in {"succeeded", "failed", "cancelled"}:
            return result
        time.sleep(0.01)
    raise AssertionError(f"Job did not finish: {result}")


def test_protected_api_search_settings_credentials_and_usage(tmp_path) -> None:
    context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        search = client.get("/api/v1/search", params={"q": "RPAPL 711"})
        assert search.status_code == 200
        assert search.json()["status"] == "ok"
        assert search.json()["mode"] == "search"
        assert search.json()["provenance"]["generation_id"]
        assert search.json()["results"][0]["citation"] == "RPAPL § 711"
        post_search = client.post(
            "/api/v1/search",
            headers=_mutation_headers(csrf),
            json={"query": "RPAPL 711"},
        )
        assert post_search.json()["results"][0]["citation"] == "RPAPL § 711"

        settings = client.get("/api/v1/settings").json()
        assert all("key" not in key.lower() for key in settings)
        profiles = client.get("/api/v1/profiles").json()["profiles"]
        openai_answer = next(
            item for item in profiles if item["id"] == "openai-answer-luna-v1"
        )
        assert openai_answer["version"] == 1
        assert openai_answer["endpoint"] == "https://api.openai.com/v1/responses"
        assert openai_answer["stores_response"] is False
        assert openai_answer["price_source"].startswith("https://")
        updated = client.put(
            "/api/v1/settings",
            headers=_mutation_headers(csrf),
            json={"monthly_budget_usd": "12.50"},
        )
        assert updated.status_code == 200
        assert updated.json()["settings"]["monthly_budget_usd"] == "12.50"
        patched = client.patch(
            "/api/v1/settings",
            headers=_mutation_headers(csrf),
            json={
                "monthly_budget_usd": "12.00",
                "per_operation_budget_usd": "1.50",
                "max_concurrent_paid_requests": 1,
                "answer_deadline_seconds": 75,
                "offline": True,
                "property_cache_max_mb": 250,
                "property_cache_retention_days": 14,
                "operational_retention_days": 45,
                "usage_retention_months": 18,
            },
        )
        assert patched.json()["settings"]["monthly_budget_usd"] == "12.00"
        assert patched.json()["settings"]["per_operation_budget_usd"] == "1.50"
        assert patched.json()["settings"]["max_concurrent_paid_requests"] == 1
        assert patched.json()["settings"]["answer_deadline_seconds"] == 75
        assert patched.json()["settings"]["offline"] is True
        assert patched.json()["settings"]["property_cache_max_mb"] == 250
        assert patched.json()["settings"]["property_cache_retention_days"] == 14
        assert patched.json()["settings"]["operational_retention_days"] == 45
        assert patched.json()["settings"]["usage_retention_months"] == 18

        retention = client.post(
            "/api/v1/maintenance/prune",
            headers=_mutation_headers(csrf),
            json={"apply": False},
        )
        assert retention.status_code == 200
        assert retention.json()["applied"] is False

        credential = "sk-never-return-this-fixture-value"
        stored = client.put(
            "/api/v1/credentials/openai",
            headers=_mutation_headers(csrf),
            json={"credential": credential, "storage": "file"},
        )
        assert stored.status_code == 200
        assert stored.json()["profiles_activated"] is True
        assert credential not in stored.text
        locked_settings = client.get("/api/v1/settings").json()
        assert locked_settings["answer_profile"] == "openai-answer-luna-v1"
        assert (
            locked_settings["embedding_profile"]
            == "openai-embedding-3-small-v1"
        )
        generic_stored = client.post(
            "/api/v1/credentials",
            headers=_mutation_headers(csrf),
            json={
                "provider": "openai",
                "credential": credential,
                "storage": "file",
            },
        )
        assert generic_stored.status_code == 200
        assert credential not in generic_stored.text
        rejected_secret = client.patch(
            "/api/v1/settings",
            headers=_mutation_headers(csrf),
            json={"api_key": credential},
        )
        assert rejected_secret.status_code == 400
        rejected_model_choice = client.patch(
            "/api/v1/settings",
            headers=_mutation_headers(csrf),
            json={"answer_profile": "fake-answer-small"},
        )
        assert rejected_model_choice.status_code == 400
        validation_estimate = client.get(
            "/api/v1/credentials/openai/validation-estimate"
        )
        assert validation_estimate.status_code == 200
        assert validation_estimate.json()["estimated_cost_usd"] == "1.2E-7"
        unapproved_validation = client.post(
            "/api/v1/credentials/openai/validate",
            headers=_mutation_headers(csrf),
            json={
                "approve_cost": False,
                "max_cost_usd": validation_estimate.json()["estimated_cost_usd"],
            },
        )
        assert unapproved_validation.status_code == 409
        assert "explicit cost approval" in unapproved_validation.json()["error"]
        assert credential not in client.get("/api/v1/credentials").text
        usage = client.get("/api/v1/usage").json()
        assert usage["scope"] == "this local installation only"
        assert usage["max_concurrent_paid_requests"] == 1

    assert credential not in context.paths.config_file.read_text(encoding="utf-8")
    diagnostic_text = "".join(
        path.read_text(encoding="utf-8") for path in context.paths.logs.glob("*.log")
    )
    assert "http_error_response" in diagnostic_text
    assert credential not in diagnostic_text


def test_answer_job_exposes_evidence_and_keeps_question_memory_only(tmp_path) -> None:
    context, app = _prepared_app(tmp_path)
    question = "What are RPAPL section 711 summary proceedings?"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        created = client.post(
            "/api/v1/query",
            headers=_mutation_headers(csrf),
            json={"question": question},
        )
        assert created.status_code == 202
        assert created.json()["status"] == "accepted"
        assert created.json()["mode"] == "answer"
        job_id = created.json()["job_id"]
        result = None
        for _ in range(100):
            result = client.get(f"/api/v1/jobs/{job_id}").json()
            if result["state"] in {"succeeded", "failed"}:
                break
            time.sleep(0.01)
        assert result is not None
        assert result["state"] == "succeeded"
        assert result["evidence"][0]["citation"] == "RPAPL § 711"
        assert result["evidence"][0]["publisher"] == "New York State Senate"
        assert result["evidence"][0]["retrieved_at"]
        assert result["evidence"][0]["last_checked_at"]
        assert result["result"]["status"] == "synthetic_demo"
        assert result["result"]["prompt_version"] == "local-answer-v1"
        assert result["provenance"]["retrieval_method"] == "exact+keyword"
        assert result["provenance"]["operation_id"] == job_id
        assert result["result"]["operation_id"] == job_id
        assert result["partial_answer"] == result["result"]["answer"]

        with client.stream("GET", f"/api/v1/jobs/{job_id}/stream") as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith(
                "application/x-ndjson"
            )
            snapshots = [line for line in stream.iter_lines() if line]
        assert len(snapshots) == 1
        assert '"state":"succeeded"' in snapshots[0]

        exported = client.post(
            "/api/v1/exports",
            headers=_mutation_headers(csrf),
            json={"kind": "answer", "job_id": job_id, "format": "json"},
        )
        assert exported.status_code == 200
        assert exported.json()["status"] == "created"
        assert exported.json()["mode"] == "answer_export"
        assert exported.json()["provenance"]["job_id"] == job_id
        assert client.get(exported.json()["download_url"]).status_code == 200

    assert question not in context.paths.state_database.read_bytes().decode("latin-1")


def test_local_route_is_conservative_and_supports_explicit_property_mode(
    tmp_path,
) -> None:
    _context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        headers = _mutation_headers(csrf)
        property_route = client.post(
            "/api/v1/route",
            headers=headers,
            json={
                "question": "Show HPD violations for building ID 375411",
                "mode": "auto",
            },
        )
        assert property_route.status_code == 200
        assert property_route.json()["mode"] == "property"
        assert property_route.json()["query"]["building_id"] == "375411"

        legal_route = client.post(
            "/api/v1/route",
            headers=headers,
            json={
                "question": (
                    "What does Housing Maintenance Code section 27-2005 require "
                    "for building ID 375411?"
                ),
                "mode": "auto",
            },
        )
        assert legal_route.status_code == 200
        assert legal_route.json()["mode"] == "search"

        unresolved = client.post(
            "/api/v1/route",
            headers=headers,
            json={"question": "Show HPD violations", "mode": "property"},
        )
        assert unresolved.status_code == 200
        assert unresolved.json()["query"] is None
        assert "building ID" in unresolved.json()["message"]


def test_paid_and_state_routes_require_csrf(tmp_path) -> None:
    _context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        _authenticate(client, app)
        response = client.post(
            "/api/v1/query",
            headers={"Origin": "http://127.0.0.1"},
            json={"question": "RPAPL 711"},
        )
        assert response.status_code == 401


def test_browser_corpus_job_updates_verifies_and_rolls_back(
    tmp_path, monkeypatch
) -> None:
    def downloader(_context, slugs, *, progress):
        assert slugs == ["ny-rpapl"]
        progress("Downloading source 1/1: ny-rpapl")
        return [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Revised browser-managed source text.",
                source_url=("https://legislation.nysenate.gov/pdf/laws/RPA?full=true"),
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(
        "app.jobs.maintenance.download_source_artifacts",
        downloader,
    )
    _context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        unapproved = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={"operation": "update", "source": "ny-rpapl"},
        )
        assert unapproved.status_code == 202
        rejected = _wait_job(client, unapproved.json()["id"])
        assert rejected["state"] == "failed"
        assert "missing source" in rejected["error_message"]

        created = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={
                "operation": "update",
                "source": "ny-rpapl",
                "allow_partial": True,
            },
        )
        assert created.status_code == 202
        assert created.json()["target_id"] == "corpus:core"
        result = _wait_job(client, created.json()["id"])
        assert result["state"] == "succeeded"
        assert result["resume"]["generation_id"]

        verified = client.post(
            "/api/v1/corpus/verify",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert verified.status_code == 200
        assert verified.json()["chunk_count"] == 1
        search = client.get("/api/v1/search", params={"q": "browser-managed"})
        assert search.json()["results"][0]["citation"] == "RPAPL § 711"

        rolled_back = client.post(
            "/api/v1/corpus/rollback",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert rolled_back.status_code == 200
        assert rolled_back.json()["generation_id"] != result["resume"]["generation_id"]


def test_browser_estimates_and_builds_semantic_index_as_durable_job(
    tmp_path,
) -> None:
    _context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        estimate = client.get("/api/v1/corpus/index-estimate")
        assert estimate.status_code == 200
        assert estimate.json()["profile_id"] == "fake-small-16"
        assert estimate.json()["chunks_requiring_embedding"] == 1
        assert estimate.json()["estimated_cost_usd"] == "0E-8"
        assert estimate.json()["approval_required"] is False
        assert estimate.json()["credential_present"] is True

        created = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={
                "operation": "index",
                "approve_cost": False,
                "max_cost_usd": "0",
                "batch_size": 1,
            },
        )
        assert created.status_code == 202
        assert created.json()["job_type"] == "corpus_index"
        assert created.json()["target_id"] == "corpus:core"
        completed = _wait_job(client, created.json()["id"])
        assert completed["state"] == "succeeded"
        assert completed["resume"]["profile_id"] == "fake-small-16"
        assert completed["resume"]["embedded_chunks"] == 1

        sources = client.get("/api/v1/sources").json()
        assert sources["readiness"] == "hybrid_ready"
        assert sources["embedding_ready_count"] == sources["chunk_count"] == 1


def test_paid_browser_index_requires_approval_ceiling_and_credential(
    tmp_path,
) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    settings = replace(
        context.settings, embedding_profile="openai-embedding-3-small-v1"
    )
    save_local_settings(context.paths, settings)
    context = replace(context, settings=settings)
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings.",
                source_url=("https://legislation.nysenate.gov/pdf/laws/RPA?full=true"),
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    storage.close()
    app = create_local_app(context)

    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        estimate = client.get("/api/v1/corpus/index-estimate").json()
        assert estimate["approval_required"] is True
        assert estimate["credential_present"] is False

        unapproved = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={"operation": "index", "max_cost_usd": "1.00"},
        )
        assert unapproved.status_code == 409
        assert "explicit cost approval" in unapproved.json()["error"]

        no_ceiling = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={"operation": "index", "approve_cost": True},
        )
        assert no_ceiling.status_code == 409
        assert "cost ceiling" in no_ceiling.json()["error"]

        missing_credential = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={
                "operation": "index",
                "approve_cost": True,
                "max_cost_usd": "1.00",
            },
        )
        assert missing_credential.status_code == 409
        assert "credential is missing" in missing_credential.json()["error"]


def test_failed_browser_corpus_job_can_resume_under_same_id(
    tmp_path, monkeypatch
) -> None:
    attempts = 0

    def downloader(_context, slugs, *, progress):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary publisher failure")
        progress("Downloading source 1/1: ny-rpapl")
        return [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Resumed source text.",
                source_url=("https://legislation.nysenate.gov/pdf/laws/RPA?full=true"),
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 15, tzinfo=UTC),
            )
        ]

    monkeypatch.setattr(
        "app.jobs.maintenance.download_source_artifacts",
        downloader,
    )
    _context, app = _prepared_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        csrf = _authenticate(client, app)
        created = client.post(
            "/api/v1/corpus/jobs",
            headers=_mutation_headers(csrf),
            json={
                "operation": "update",
                "source": "ny-rpapl",
                "allow_partial": True,
            },
        )
        job_id = created.json()["id"]
        failed = _wait_job(client, job_id)
        assert failed["state"] == "failed"
        assert failed["retryable"] is True

        resumed = client.post(
            f"/api/v1/jobs/{job_id}/resume",
            headers=_mutation_headers(csrf),
            json={},
        )
        assert resumed.status_code == 202
        assert resumed.json()["id"] == job_id
        completed = _wait_job(client, job_id)
        assert completed["state"] == "succeeded"
        assert attempts == 2
