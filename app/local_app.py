from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from app import __version__
from app.corpus.service import CorpusService
from app.credentials.store import (
    CredentialResolver,
    CredentialStoreError,
    configured_credential_slots,
    validate_credential,
)
from app.exporting.service import ExportError, ResearchExporter, safe_export_path
from app.hpd.cache import CachedPropertyRepository, PropertyCacheError
from app.hpd.connector import (
    HpdSocrataConnector,
    PropertyConnectorError,
    PropertyQuery,
    PropertySearchResponse,
    load_hpd_manifest,
)
from app.hpd.summary import PropertySummaryService
from app.jobs.interactive import InteractiveAnswerJobs
from app.jobs.maintenance import CorpusMaintenanceJobs, job_payload
from app.jobs.mutations import run_corpus_mutation
from app.jobs.property_exports import PropertyExportJobs
from app.jobs.runtime import Deadline, OperationDeadlineExceeded
from app.jobs.service import (
    InvalidJobTransition,
    JobConflict,
    JobNotFound,
)
from app.maintenance.diagnostics import workspace_status
from app.maintenance.logging import LocalDiagnosticLog
from app.maintenance.retention import RetentionError, WorkspaceRetentionService
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    configured_profile_entries,
    get_configured_profile,
)
from app.providers.validation import (
    credential_validation_estimate,
    run_credential_validation,
)
from app.query_routing.router import classify_query, hpd_request_from_question
from app.retrieval.local import LocalSearch, LocalSearchFilters
from app.security.local_session import (
    SESSION_COOKIE,
    LocalSessionError,
    LocalSessionService,
)
from app.storage.database import LocalStorage
from app.storage.schema import maintenance_state
from app.usage.ledger import PaidCapacityUnavailable, SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied
from app.workspace.settings import LocalSettingsError, save_local_settings

APP_DIRECTORY = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIRECTORY / "templates"))

DEMO_RESULTS = [
    {
        "citation": "DEMO § 1",
        "title": "Synthetic demonstration source",
        "excerpt": (
            "This generated sample proves that the local interface is working. "
            "It is not NYC law and must not be used as legal authority."
        ),
    }
]


def create_local_app(
    context: WorkspaceContext, *, property_connector_factory=None
) -> FastAPI:
    if not context.initialized:
        raise ValueError("The local browser app requires an initialized workspace.")
    storage = LocalStorage.open(context.paths)
    sessions = LocalSessionService(storage)
    launch_token = sessions.begin_process()
    interactive_jobs = InteractiveAnswerJobs(context, storage)
    maintenance_jobs = CorpusMaintenanceJobs(context, storage)
    property_export_jobs = PropertyExportJobs(
        context,
        storage,
        connector_factory=property_connector_factory,
    )
    diagnostic_log = LocalDiagnosticLog(context.paths.logs)
    diagnostic_log.record("application_started", version=__version__)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            interactive_jobs.close()
            maintenance_jobs.close()
            property_export_jobs.close()
            storage.close()

    app = FastAPI(
        title=context.settings.app_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.workspace = context
    app.state.storage = storage
    app.state.launch_token = launch_token
    app.mount(
        "/static",
        StaticFiles(directory=str(APP_DIRECTORY / "static")),
        name="static",
    )

    @app.middleware("http")
    async def local_security_boundary(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            diagnostic_log.record(
                "security_rejection",
                method=request.method,
                path=request.url.path,
                reason="host",
            )
            return _security_error("Unrecognized local Host header.", 400)
        exempt = request.url.path in {
            "/",
            "/api/v1/local-session",
            "/api/v1/session/exchange",
            "/api/v1/demo/search",
        } or request.url.path.startswith("/static/")
        changing = request.method not in {"GET", "HEAD", "OPTIONS"}
        if changing and not _same_origin(request):
            diagnostic_log.record(
                "security_rejection",
                method=request.method,
                path=request.url.path,
                reason="origin",
            )
            return _security_error("Same-origin request required.", 403)
        if not exempt:
            valid = sessions.validate(
                request.cookies.get(SESSION_COOKIE),
                csrf_token=request.headers.get("x-csrf-token"),
                require_csrf=changing,
            )
            if not valid:
                diagnostic_log.record(
                    "security_rejection",
                    method=request.method,
                    path=request.url.path,
                    reason="session",
                )
                return _security_error("Local session authentication required.", 401)
        try:
            response = await call_next(request)
        except Exception as exc:
            diagnostic_log.record(
                "http_exception",
                method=request.method,
                path=request.url.path,
                error_type=type(exc).__name__,
            )
            raise
        if response.status_code >= 400:
            diagnostic_log.record(
                "http_error_response",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    def local_home(request: Request):
        return templates.TemplateResponse(
            request,
            "local.html",
            {
                "app_name": context.settings.app_name,
                "initialized": context.initialized,
                "offline": context.settings.offline,
            },
        )

    @app.post("/api/v1/local-session")
    @app.post("/api/v1/session/exchange")
    async def exchange_session(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return _security_error("A launch credential is required.", 400)
        try:
            exchanged = sessions.exchange(str(payload.get("launch_token", "")))
        except (AttributeError, LocalSessionError):
            return _security_error("Invalid or expired launch credential.", 401)
        response = JSONResponse(
            {
                "status": "authenticated",
                "csrf_token": exchanged.csrf_token,
                "expires_at": exchanged.expires_at.isoformat(),
            }
        )
        response.set_cookie(
            SESSION_COOKIE,
            exchanged.token,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
            max_age=12 * 60 * 60,
        )
        return response

    @app.get("/api/v1/status")
    def local_status() -> JSONResponse:
        payload = workspace_status(context, storage)
        payload["mode"] = "local"
        capabilities = payload["capabilities"]
        capabilities["synthetic_demo"] = True
        capabilities["legal_corpus"] = bool(
            payload["legal_corpus"]["active_generation_id"]
        )
        return JSONResponse(payload)

    @app.get("/api/v1/session/csrf")
    def refresh_csrf(request: Request) -> JSONResponse:
        try:
            token = sessions.rotate_csrf(request.cookies.get(SESSION_COOKIE))
        except LocalSessionError as exc:
            return _security_error(str(exc), 401)
        return JSONResponse({"csrf_token": token})

    @app.get("/api/v1/sources")
    def source_status() -> JSONResponse:
        service = CorpusService(storage)
        status = service.status()
        return JSONResponse(
            {
                "active_generation_id": status.active_generation_id,
                "readiness": status.readiness,
                "is_partial": status.is_partial,
                "chunk_count": status.chunk_count,
                "embedding_ready_count": status.embedding_ready_count,
                "sources": service.source_statuses(),
            }
        )

    @app.get("/api/v1/settings")
    def settings_status() -> JSONResponse:
        return JSONResponse(context.settings.public_dict())

    @app.patch("/api/v1/settings")
    @app.put("/api/v1/settings")
    async def update_settings(request: Request) -> JSONResponse:
        nonlocal context
        try:
            with storage.state_engine.connect() as connection:
                if connection.scalar(
                    select(maintenance_state.c.active).where(
                        maintenance_state.c.id == 1
                    )
                ):
                    return JSONResponse(
                        {"error": "Workspace maintenance is active."},
                        status_code=409,
                    )
            payload = await request.json()
            allowed = {
                "monthly_budget_usd",
                "per_operation_budget_usd",
                "max_concurrent_paid_requests",
                "answer_deadline_seconds",
                "answer_profile",
                "embedding_profile",
                "offline",
                "property_cache_max_mb",
                "property_cache_retention_days",
                "operational_retention_days",
                "usage_retention_months",
            }
            if not isinstance(payload, dict) or not set(payload) <= allowed:
                raise LocalSettingsError("Unsupported or invalid settings field.")
            candidate = replace(context.settings, **payload).validate()
            save_local_settings(context.paths, candidate)
            context = replace(context, settings=candidate)
            app.state.workspace = context
        except (TypeError, LocalSettingsError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {
                "settings": context.settings.public_dict(),
                "restart_required_for_active_jobs": True,
            }
        )

    @app.get("/api/v1/credentials")
    def credential_status() -> JSONResponse:
        resolver = CredentialResolver(context)
        try:
            result = [
                asdict(resolver.presence(provider))
                for provider in configured_credential_slots(context.settings)
            ]
        except CredentialStoreError as exc:
            return JSONResponse({"credentials": [], "error": str(exc)}, status_code=503)
        return JSONResponse({"credentials": result})

    @app.put("/api/v1/credentials/{provider}")
    async def replace_credential(provider: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "Credential request must be JSON."}, status_code=400
            )
        return _store_credential(context, provider, payload)

    @app.post("/api/v1/credentials")
    async def create_or_replace_credential(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            provider = str(payload.get("provider", ""))
        except Exception:
            return JSONResponse(
                {"error": "Credential request must be JSON."}, status_code=400
            )
        return _store_credential(context, provider, payload)

    @app.get("/api/v1/credentials/{provider}/validation-estimate")
    def validation_estimate(provider: str) -> JSONResponse:
        try:
            estimate = credential_validation_estimate(provider)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        payload = asdict(estimate)
        payload["estimated_cost_usd"] = str(estimate.estimated_cost_usd)
        return JSONResponse(payload)

    @app.post("/api/v1/credentials/{provider}/validate")
    async def validate_stored_credential(
        provider: str, request: Request
    ) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "approve_cost",
                "max_cost_usd",
            }:
                raise ValueError(
                    "Credential validation request contains unsupported fields."
                )
            raw_ceiling = payload.get("max_cost_usd")
            ceiling = Decimal(str(raw_ceiling)) if raw_ceiling is not None else None
            result = run_credential_validation(
                context,
                storage,
                provider,
                approve_cost=payload.get("approve_cost") is True,
                max_cost_usd=ceiling,
            )
        except (
            AttributeError,
            CredentialStoreError,
            NetworkAccessDenied,
            PaidCapacityUnavailable,
            ProviderExecutionError,
            SpendDenied,
            TypeError,
            ValueError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        response = asdict(result)
        response["cost_usd"] = str(result.cost_usd)
        response["warning"] = (
            "This validates only the configured operation/profile; provider "
            "availability and account limits can change."
        )
        return JSONResponse(response)

    @app.get("/api/v1/usage")
    def usage_status() -> JSONResponse:
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal(context.settings.monthly_budget_usd),
            timezone=context.settings.budget_timezone,
        )
        return JSONResponse(
            {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in asdict(summary).items()
            }
            | {
                "scope": "this local installation only",
                "max_concurrent_paid_requests": (
                    context.settings.max_concurrent_paid_requests
                ),
            }
        )

    @app.post("/api/v1/demo/search")
    def demo_search() -> JSONResponse:
        return JSONResponse(
            {
                "status": "synthetic_demo",
                "warning": "Synthetic demonstration content; not legal authority.",
                "results": DEMO_RESULTS,
            }
        )

    @app.get("/api/v1/search")
    def legal_search(
        q: str,
        source: str | None = None,
        source_type: str | None = None,
        jurisdiction: str | None = None,
        limit: int = 10,
    ) -> JSONResponse:
        try:
            corpus = CorpusService(storage).status()
            result = LocalSearch(storage).search(
                q,
                filters=LocalSearchFilters(
                    source_slug=source,
                    source_type=source_type,
                    jurisdiction=jurisdiction,
                ),
                limit=limit,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        warnings = []
        if corpus.is_partial:
            warnings.append("The active legal corpus is partial.")
        if result.semantic_status != "ready":
            warnings.append(
                "Semantic retrieval is unavailable; results use exact/keyword search."
            )
        return JSONResponse(
            {
                "status": "ok" if result.results else "insufficient_coverage",
                "mode": "search",
                "generation_id": result.generation_id,
                "method": result.method,
                "semantic_status": result.semantic_status,
                "coverage": {
                    "is_partial": corpus.is_partial,
                    "source_count": corpus.source_count,
                    "source_filter": source,
                },
                "warnings": warnings,
                "provenance": {
                    "generation_id": result.generation_id,
                    "retrieval_method": result.method,
                },
                "results": [
                    {
                        "chunk_id": item.chunk_id,
                        "source_slug": item.source_slug,
                        "source_name": item.source_name,
                        "source_type": item.source_type,
                        "jurisdiction": item.jurisdiction,
                        "source_url": item.source_url,
                        "citation": item.citation,
                        "title": item.title,
                        "text": item.text,
                        "score": item.score,
                        "match_types": item.match_types,
                    }
                    for item in result.results
                ],
            }
        )

    @app.post("/api/v1/search")
    async def legal_search_post(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "q",
                "query",
                "source",
                "source_type",
                "jurisdiction",
                "limit",
            }:
                raise ValueError("Search request contains unsupported fields.")
            query = str(payload.get("q") or payload.get("query") or "")
            limit = int(payload.get("limit", 10))
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return legal_search(
            query,
            source=payload.get("source"),
            source_type=payload.get("source_type"),
            jurisdiction=payload.get("jurisdiction"),
            limit=limit,
        )

    @app.post("/api/v1/query")
    async def start_answer(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "question",
                "source",
                "limit",
                "allow_unknown_cost",
            }:
                raise ValueError("Answer request contains unsupported fields.")
            if "allow_unknown_cost" in payload and not isinstance(
                payload["allow_unknown_cost"], bool
            ):
                raise ValueError("allow_unknown_cost must be true or false.")
            question = str(payload.get("question", ""))
            limit = int(payload.get("limit", 8))
            if not question.strip() or len(question) > 4_000 or not 1 <= limit <= 20:
                raise ValueError("Question or result limit is invalid.")
            job_id = interactive_jobs.submit(
                question,
                filters=LocalSearchFilters(source_slug=payload.get("source")),
                limit=limit,
                allow_unknown_cost=payload.get("allow_unknown_cost") is True,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        corpus = CorpusService(storage).status()
        answer_profile = get_configured_profile(
            context.settings, ProfileKind.ANSWER
        )
        warnings = ["The active legal corpus is partial."] if corpus.is_partial else []
        if payload.get("allow_unknown_cost") is True:
            warnings.append(
                "Unknown provider cost: this one-off request is outside USD "
                "budget caps."
            )
        return JSONResponse(
            {
                "status": "accepted",
                "mode": "answer",
                "job_id": job_id,
                "state": "queued",
                "coverage": {
                    "is_partial": corpus.is_partial,
                    "source_count": corpus.source_count,
                    "source_filter": payload.get("source"),
                },
                "warnings": warnings,
                "provenance": {
                    "generation_id": corpus.active_generation_id,
                    "answer_profile_id": answer_profile.id,
                    "cost_known": answer_profile.pricing_verified,
                },
            },
            status_code=202,
        )

    @app.post("/api/v1/route")
    async def plan_research_route(request: Request) -> JSONResponse:
        """Classify a typed research request without contacting an external service."""
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "question",
                "mode",
                "limit",
            }:
                raise ValueError("Route request contains unsupported fields.")
            question = str(payload.get("question", "")).strip()
            mode = str(payload.get("mode", "auto"))
            limit = int(payload.get("limit", 50))
            if not question or len(question) > 4_000 or not 1 <= limit <= 100:
                raise ValueError("Question or result limit is invalid.")
            if mode not in {"auto", "property"}:
                raise ValueError("Route mode must be auto or property.")
            route = classify_query(question)
            if mode == "property" or route.kind == "property":
                try:
                    legacy_request = hpd_request_from_question(question, limit)
                except ValueError as exc:
                    return JSONResponse(
                        {
                            "mode": "property",
                            "route_reason": route.reason,
                            "query": None,
                            "message": str(exc),
                        }
                    )
                values = legacy_request.model_dump(exclude_none=True)
                query = {
                    "building_id": values.get("building_id"),
                    "registration_id": values.get("registration_id"),
                    "house_number": values.get("house_number"),
                    "street_name": values.get("street_name"),
                    "zip_code": values.get("zip_code"),
                    "borough": values.get("boro"),
                    "limit": values.get("limit", limit),
                }
                query = {
                    key: value for key, value in query.items() if value is not None
                }
                PropertyQuery(**query).validate()
                return JSONResponse(
                    {
                        "mode": "property",
                        "route_reason": route.reason,
                        "query": query,
                        "message": (
                            "Confirm the resolved property before relying on records."
                        ),
                    }
                )
            return JSONResponse(
                {
                    "mode": "search",
                    "route_reason": route.reason,
                    "query": None,
                    "message": "Searching installed legal sources locally.",
                }
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/v1/corpus/jobs")
    async def start_corpus_job(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "operation",
                "source",
                "allow_partial",
                "approve_cost",
                "max_cost_usd",
                "batch_size",
            }:
                raise ValueError("Corpus job request contains unsupported fields.")
            raw_ceiling = payload.get("max_cost_usd")
            if "allow_partial" in payload and not isinstance(
                payload["allow_partial"], bool
            ):
                raise ValueError("allow_partial must be true or false.")
            max_cost_usd = (
                Decimal(str(raw_ceiling)) if raw_ceiling is not None else None
            )
            job = maintenance_jobs.submit(
                str(payload.get("operation", "")),
                source=payload.get("source"),
                allow_partial=payload.get("allow_partial") is True,
                approve_cost=payload.get("approve_cost") is True,
                max_cost_usd=max_cost_usd,
                batch_size=int(payload.get("batch_size", 32)),
            )
        except (
            AttributeError,
            CredentialStoreError,
            JobConflict,
            TypeError,
            ValueError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(job_payload(job), status_code=202)

    @app.get("/api/v1/corpus/index-estimate")
    def corpus_index_estimate() -> JSONResponse:
        try:
            estimate, paid, credential_source = maintenance_jobs.estimate_index()
            profile = get_configured_profile(context.settings, ProfileKind.EMBEDDING)
        except (CredentialStoreError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        payload = asdict(estimate)
        payload["estimated_cost_usd"] = str(estimate.estimated_cost_usd)
        payload |= {
            "provider": profile.provider,
            "model": profile.model,
            "paid": paid,
            "approval_required": paid,
            "credential_present": not paid or credential_source is not None,
            "credential_source": credential_source,
            "price_effective_date": profile.price_effective_date,
            "price_source": profile.price_source,
        }
        return JSONResponse(payload)

    @app.get("/api/v1/jobs")
    def list_jobs(limit: int = 50) -> JSONResponse:
        if not 1 <= limit <= 100:
            return JSONResponse(
                {"error": "Job limit must be between 1 and 100."},
                status_code=400,
            )
        return JSONResponse(
            {"jobs": [job_payload(job) for job in maintenance_jobs.list(limit=limit)]}
        )

    @app.get("/api/v1/jobs/{job_id}")
    def answer_job(job_id: str) -> JSONResponse:
        try:
            job = maintenance_jobs.get(job_id)
            payload = (
                interactive_jobs.get(job_id)
                if job.job_type == "answer"
                else job_payload(job)
            )
        except JobNotFound:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        return JSONResponse(payload)

    @app.get("/api/v1/jobs/{job_id}/stream")
    async def stream_answer_job(job_id: str, request: Request):
        try:
            job = maintenance_jobs.get(job_id)
            if job.job_type != "answer":
                return JSONResponse(
                    {"error": "Only answer jobs can be streamed."},
                    status_code=400,
                )
            interactive_jobs.get(job_id)
        except JobNotFound:
            return JSONResponse({"error": "Job not found."}, status_code=404)

        async def snapshots():
            previous = None
            while not await request.is_disconnected():
                try:
                    payload = interactive_jobs.get(job_id)
                except JobNotFound:
                    break
                encoded = json.dumps(payload, separators=(",", ":"))
                if encoded != previous:
                    yield f"{encoded}\n"
                    previous = encoded
                if payload["state"] in {"succeeded", "failed", "cancelled"}:
                    break
                await asyncio.sleep(0.075)

        return StreamingResponse(
            snapshots(),
            media_type="application/x-ndjson",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/jobs/{job_id}/export")
    async def export_answer_job(job_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            output_format = str(payload.get("format", "markdown"))
            job = interactive_jobs.get(job_id)
            if job["result"] is None:
                return JSONResponse(
                    {"error": "A completed in-memory result is required."},
                    status_code=409,
                )
            path = ResearchExporter(context.paths).answer(
                job["result"], output_format=output_format
            )
        except JobNotFound:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        except (AttributeError, ExportError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {"filename": path.name, "download_url": f"/api/v1/exports/{path.name}"}
        )

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_answer_job(job_id: str) -> JSONResponse:
        try:
            job = maintenance_jobs.get(job_id)
            payload = (
                interactive_jobs.cancel(job_id)
                if job.job_type == "answer"
                else job_payload(maintenance_jobs.cancel(job_id))
            )
        except (JobNotFound, RuntimeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(payload)

    @app.post("/api/v1/jobs/{job_id}/resume")
    def resume_maintenance_job(job_id: str) -> JSONResponse:
        try:
            existing = maintenance_jobs.get(job_id)
            resumed = (
                property_export_jobs.resume(job_id)
                if existing.job_type == "property_complete_export"
                else maintenance_jobs.resume(job_id)
            )
            payload = job_payload(resumed)
        except (InvalidJobTransition, JobConflict, JobNotFound, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(payload, status_code=202)

    @app.post("/api/v1/corpus/verify")
    def verify_corpus() -> JSONResponse:
        try:
            payload = CorpusService(storage).verify()
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(payload)

    @app.post("/api/v1/corpus/rollback")
    def rollback_corpus() -> JSONResponse:
        try:
            job, generation_id = run_corpus_mutation(
                storage,
                "corpus_rollback",
                CorpusService(storage).rollback,
            )
        except (JobConflict, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(
            {
                "status": "active",
                "job_id": job.id,
                "generation_id": generation_id,
            }
        )

    @app.get("/api/v1/profiles")
    def provider_profiles() -> JSONResponse:
        return JSONResponse(
            {
                "selected": {
                    "answer": context.settings.answer_profile,
                    "embedding": context.settings.embedding_profile,
                },
                "profiles": [
                    {
                        "id": selection_id,
                        "effective_profile_id": profile.id,
                        "version": profile.version,
                        "kind": profile.kind.value,
                        "provider": profile.provider,
                        "credential_slot": profile.credential_slot,
                        "model": profile.model,
                        "endpoint": profile.endpoint,
                        "dimension": profile.dimension,
                        "input_usd_per_million": (
                            str(profile.input_usd_per_million)
                            if profile.input_usd_per_million is not None
                            else None
                        ),
                        "output_usd_per_million": (
                            str(profile.output_usd_per_million)
                            if profile.output_usd_per_million is not None
                            else None
                        ),
                        "token_estimator": profile.token_estimator,
                        "request_timeout_seconds": profile.request_timeout_seconds,
                        "max_attempts": profile.max_attempts,
                        "pricing_verified": profile.pricing_verified,
                        "price_effective_date": profile.price_effective_date,
                        "price_source": profile.price_source,
                        "stores_response": profile.stores_response,
                        "compatibility_verified": profile.compatibility_verified,
                    }
                    for selection_id, profile in configured_profile_entries(
                        context.settings
                    )
                ],
            }
        )

    @app.post("/api/v1/properties/search")
    async def property_search(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            refresh = bool(payload.pop("refresh", False))
            query = _property_query(payload)
            connector, repository = _property_repository(
                context,
                storage,
                property_connector_factory,
            )
            try:
                result = repository.search(
                    query, refresh=refresh, deadline=Deadline.after(15)
                )
            finally:
                connector.close()
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (
            CredentialStoreError,
            NetworkAccessDenied,
            OperationDeadlineExceeded,
            PropertyCacheError,
            PropertyConnectorError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse(_property_response_payload(result))

    @app.post("/api/v1/properties/summarize")
    async def property_summarize(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Property summary request must be JSON.")
            allow_unknown_cost = payload.pop("allow_unknown_cost", False)
            if not isinstance(allow_unknown_cost, bool):
                raise ValueError("allow_unknown_cost must be true or false.")
            question = str(
                payload.pop(
                    "question",
                    "Summarize these HPD violations and source limitations.",
                )
            )
            query = _property_query(payload)
            connector, repository = _property_repository(
                context,
                storage,
                property_connector_factory,
            )
            try:
                fixed_result = repository.cached(query, allow_stale=True)
            finally:
                connector.close()
            gateway = ProviderGateway(context, UsageLedger(storage))
            try:
                result = PropertySummaryService(
                    context,
                    storage,
                    gateway,
                    CredentialResolver(context),
                ).summarize(
                    fixed_result,
                    question=question,
                    deadline=Deadline.after(context.settings.answer_deadline_seconds),
                    allow_unknown_cost=allow_unknown_cost,
                )
            finally:
                gateway.close()
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (CredentialStoreError, PropertyCacheError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        response = asdict(result)
        response |= {
            "mode": "property_summary",
            "coverage": {
                "property_evidence_count": len(result.property_evidence_ids),
                "legal_evidence_count": len(result.legal_evidence),
                "property_is_complete": result.property_is_complete,
                "omitted_property_rows": result.omitted_property_rows,
            },
            "warnings": (
                ([] if result.property_is_complete else [
                    "The summarized property page is not a complete history."
                ])
                + (
                    []
                    if result.cost_known
                    else [
                        "Unknown provider cost: this one-off request was outside "
                        "USD budget caps."
                    ]
                )
            ),
            "provenance": {
                "operation_id": result.operation_id,
                "profile_id": result.profile_id,
                "property_fetched_at": result.property_fetched_at,
                "property_fetch_started_at": result.property_fetch_started_at,
                "property_fetch_completed_at": result.property_fetch_completed_at,
                "property_cache_status": result.property_cache_status,
                "prompt_version": result.prompt_version,
            },
        }
        return JSONResponse(response)

    @app.delete("/api/v1/properties/cache")
    def clear_property_cache() -> JSONResponse:
        connector, repository = _property_repository(
            context,
            storage,
            property_connector_factory,
        )
        try:
            removed = repository.clear()
        finally:
            connector.close()
        return JSONResponse({"removed_entries": removed})

    @app.post("/api/v1/maintenance/prune")
    async def prune_retained_data(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {"apply"}:
                raise ValueError("Retention request contains unsupported fields.")
            if "apply" in payload and not isinstance(payload["apply"], bool):
                raise ValueError("Retention apply must be true or false.")
            result = WorkspaceRetentionService(storage, context.settings).run(
                apply=payload.get("apply") is True
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except RetentionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(result.as_dict())

    @app.post("/api/v1/properties/export")
    async def export_property_result(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            query = _property_query(payload)
            connector, repository = _property_repository(
                context,
                storage,
                property_connector_factory,
            )
            try:
                fixed_result = repository.cached(query, allow_stale=True)
            finally:
                connector.close()
            path = ResearchExporter(context.paths).property_csv(fixed_result)
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (CredentialStoreError, PropertyCacheError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(
            {"filename": path.name, "download_url": f"/api/v1/exports/{path.name}"}
        )

    @app.post("/api/v1/properties/export-complete")
    async def export_complete_property(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Property export request must be a JSON object.")
            max_pages = int(payload.pop("max_pages", 20))
            deadline_seconds = float(payload.pop("deadline_seconds", 120))
            query = _property_query(payload)
            job = property_export_jobs.submit(
                query,
                max_pages=max_pages,
                deadline_seconds=deadline_seconds,
            )
        except (
            AttributeError,
            CredentialStoreError,
            JobConflict,
            TypeError,
            ValueError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        response = job_payload(job)
        response |= {
            "status": "accepted",
            "mode": "property_complete_export",
            "coverage": {"max_pages": max_pages, "is_complete": False},
            "warnings": [
                "Live pages can change between fetches; the export is not a "
                "transactional snapshot."
            ],
            "provenance": {"connector": load_hpd_manifest()["connector_version"]},
        }
        return JSONResponse(response, status_code=202)

    @app.post("/api/v1/exports")
    async def create_scoped_export(request: Request) -> JSONResponse:
        """Compatibility contract for explicitly scoped answer/property exports."""
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Export request must be a JSON object.")
            kind = payload.get("kind")
            if kind == "answer":
                if not set(payload) <= {"kind", "job_id", "format"}:
                    raise ValueError("Answer export contains unsupported fields.")
                job_id = str(payload.get("job_id", ""))
                output_format = str(payload.get("format", "markdown"))
                job = interactive_jobs.get(job_id)
                if job["result"] is None:
                    return JSONResponse(
                        {"error": "A completed in-memory result is required."},
                        status_code=409,
                    )
                path = ResearchExporter(context.paths).answer(
                    job["result"], output_format=output_format
                )
                response = {
                    "status": "created",
                    "mode": "answer_export",
                    "coverage": {
                        "evidence_count": len(job["result"].get("evidence", []))
                    },
                    "warnings": [],
                    "provenance": {
                        "job_id": job_id,
                        "generation_id": job["result"].get("generation_id"),
                    },
                }
            elif kind == "property":
                if set(payload) != {"kind", "query"}:
                    raise ValueError(
                        "Property export requires only kind and query fields."
                    )
                query = _property_query(payload["query"])
                connector, repository = _property_repository(
                    context,
                    storage,
                    property_connector_factory,
                )
                try:
                    fixed_result = repository.cached(query, allow_stale=True)
                finally:
                    connector.close()
                path = ResearchExporter(context.paths).property_csv(fixed_result)
                response = {
                    "status": "created",
                    "mode": "property_export",
                    "coverage": {
                        "returned_count": fixed_result.returned_count,
                        "is_complete": fixed_result.is_complete,
                        "has_more": bool(fixed_result.continuation),
                    },
                    "warnings": (
                        []
                        if fixed_result.is_complete
                        else ["The exported loaded page is not a complete result set."]
                    ),
                    "provenance": {
                        "dataset_id": fixed_result.dataset_id,
                        "connector_version": fixed_result.connector_version,
                        "fetched_at": fixed_result.fetched_at.isoformat(),
                        "cache_status": fixed_result.cache_status,
                    },
                }
            elif kind == "property_complete":
                if not set(payload) <= {
                    "kind",
                    "query",
                    "max_pages",
                    "deadline_seconds",
                }:
                    raise ValueError(
                        "Complete property export contains unsupported fields."
                    )
                query_payload = payload.get("query")
                if not isinstance(query_payload, dict):
                    raise ValueError("Complete property export requires a query.")
                query = _property_query(dict(query_payload))
                max_pages = int(payload.get("max_pages", 20))
                deadline_seconds = float(payload.get("deadline_seconds", 120))
                job = property_export_jobs.submit(
                    query,
                    max_pages=max_pages,
                    deadline_seconds=deadline_seconds,
                )
                response = job_payload(job) | {
                    "status": "accepted",
                    "mode": "property_complete_export",
                    "coverage": {"max_pages": max_pages, "is_complete": False},
                    "warnings": [
                        "Live pages can change between fetches; the export is not a "
                        "transactional snapshot."
                    ],
                    "provenance": {
                        "connector": load_hpd_manifest()["connector_version"]
                    },
                }
                return JSONResponse(response, status_code=202)
            else:
                raise ValueError(
                    "Export kind must be answer, property, or property_complete."
                )
        except JobNotFound:
            return JSONResponse({"error": "Job not found."}, status_code=404)
        except (AttributeError, ExportError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (CredentialStoreError, PropertyCacheError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(
            response
            | {
                "filename": path.name,
                "download_url": f"/api/v1/exports/{path.name}",
            }
        )

    @app.get("/api/v1/exports/{filename}")
    def download_export(filename: str):
        try:
            path = safe_export_path(context.paths, filename)
        except ExportError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return FileResponse(path, filename=path.name)

    return app


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    parsed = urlsplit(origin)
    request_port = request.url.port or (443 if request.url.scheme == "https" else 80)
    origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return (
        parsed.scheme == request.url.scheme
        and parsed.hostname == request.url.hostname
        and origin_port == request_port
    )


def _security_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _store_credential(
    context: WorkspaceContext, provider: str, payload: object
) -> JSONResponse:
    try:
        if provider not in configured_credential_slots(context.settings):
            raise CredentialStoreError(
                "Credential slot is not used by a configured provider profile."
            )
        if not isinstance(payload, dict) or not set(payload) <= {
            "provider",
            "credential",
            "storage",
        }:
            raise CredentialStoreError(
                "Credential request contains unsupported fields."
            )
        value = validate_credential(provider, str(payload.get("credential", "")))
        backend = str(payload.get("storage", "keyring"))
        CredentialResolver(context).writable(backend).set(provider, value)
    except (AttributeError, CredentialStoreError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "provider": provider,
            "present": True,
            "source": "keyring" if backend == "keyring" else "secret_file",
        }
    )


def _property_query(payload: object) -> PropertyQuery:
    if not isinstance(payload, dict):
        raise ValueError("Property request must be a JSON object.")
    allowed = {
        "building_id",
        "registration_id",
        "house_number",
        "street_name",
        "borough",
        "zip_code",
        "violation_class",
        "status",
        "inspection_date_from",
        "inspection_date_to",
        "limit",
        "continuation",
    }
    if not set(payload) <= allowed:
        raise ValueError("Property request contains unsupported fields.")
    return PropertyQuery(**payload).validate()


def _property_repository(context, storage, factory):
    token, _source = CredentialResolver(context).resolve("socrata")
    connector = (
        factory(context, token)
        if factory
        else HpdSocrataConnector(context.network, app_token=token)
    )
    repository = CachedPropertyRepository(
        storage,
        connector,
        max_bytes=context.settings.property_cache_max_mb * 1024**2,
        retention_days=context.settings.property_cache_retention_days,
    )
    return connector, repository


def _property_response_payload(result: PropertySearchResponse) -> dict:
    payload = asdict(result)
    payload["fetched_at"] = result.fetched_at.isoformat()
    for field in ("source_update_time", "fetch_started_at", "fetch_completed_at"):
        value = getattr(result, field)
        payload[field] = value.isoformat() if value is not None else None
    has_more = result.has_more or bool(result.continuation)
    next_cursor = result.next_cursor or result.continuation
    payload["has_more"] = has_more
    payload["next_cursor"] = next_cursor
    notice = (
        "No matching violation rows is not proof that a building does not exist."
        if result.source_status == "verified_zero"
        else "Pages fetched at different times are not a transactional snapshot."
    )
    payload.update(
        {
            "status": result.source_status,
            "mode": "property",
            "coverage": {
                "returned_count": result.returned_count,
                "total_count": result.total_count,
                "is_complete": result.is_complete,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
            "warnings": [notice],
            "provenance": {
                "dataset_id": result.dataset_id,
                "dataset_url": result.dataset_url,
                "connector_version": result.connector_version,
                "fetched_at": result.fetched_at.isoformat(),
                "fetch_started_at": payload["fetch_started_at"],
                "fetch_completed_at": payload["fetch_completed_at"],
                "source_update_time": payload["source_update_time"],
                "cache_status": result.cache_status,
            },
            "notice": notice,
        }
    )
    return payload
