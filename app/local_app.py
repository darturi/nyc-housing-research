from __future__ import annotations

import asyncio
import hashlib
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
from starlette.datastructures import FormData, UploadFile

from app import __version__
from app.corpus.extractions import ExtractionService
from app.corpus.packs import SourcePackService
from app.corpus.resource_parsers import MAX_RESOURCE_BYTES
from app.corpus.resources import ResourceMetadata, ResourceService
from app.corpus.service import CorpusService, CorpusValidationError
from app.credentials.store import (
    CredentialResolver,
    CredentialStoreError,
    configured_credential_slots,
    validate_credential,
)
from app.exporting.service import ExportError, ResearchExporter, safe_export_path
from app.help.routing import HelpCatalogError, HelpResourceService
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
from app.jobs.resources import ResourceJobs, run_resource_mutation
from app.jobs.runtime import Deadline, OperationDeadlineExceeded
from app.jobs.service import (
    InvalidJobTransition,
    JobConflict,
    JobNotFound,
)
from app.localization.catalog import LocaleCatalogError, load_locale_catalog
from app.maintenance.diagnostics import workspace_status
from app.maintenance.logging import LocalDiagnosticLog
from app.maintenance.retention import RetentionError, WorkspaceRetentionService
from app.offline.service import OfflineExtensionError, OfflineExtensionService
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    configured_profile_entries,
    get_configured_profile,
    get_profile,
)
from app.providers.validation import (
    credential_validation_estimate,
    run_credential_validation,
)
from app.query_routing.router import classify_query, hpd_request_from_question
from app.research.comparisons import ComparisonError, SourceComparisonService
from app.research.dossiers import DossierError, PropertyDossierService
from app.research.matters import MatterConflict, MatterError, MatterService
from app.retrieval.local import LocalSearch, LocalSearchFilters
from app.security.local_session import (
    SESSION_COOKIE,
    LocalSessionError,
    LocalSessionService,
)
from app.storage.database import LocalStorage
from app.storage.schema import maintenance_state
from app.usage.ledger import PaidCapacityUnavailable, SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext, network_policy_for_settings
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
MAX_RESOURCE_REQUEST_BYTES = MAX_RESOURCE_BYTES + 1024 * 1024


def create_local_app(
    context: WorkspaceContext, *, property_connector_factory=None
) -> FastAPI:
    if not context.initialized:
        raise ValueError("The local browser app requires an initialized workspace.")
    storage = LocalStorage.open(context.paths)
    storage.assert_compatible()
    sessions = LocalSessionService(storage)
    launch_token = sessions.begin_process()
    interactive_jobs = InteractiveAnswerJobs(context, storage)
    maintenance_jobs = CorpusMaintenanceJobs(context, storage)
    property_export_jobs = PropertyExportJobs(
        context,
        storage,
        connector_factory=property_connector_factory,
    )
    resource_jobs = ResourceJobs(context, storage)
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
            resource_jobs.close()
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
        } or request.url.path.startswith(("/static/", "/api/v1/locales/"))
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
        if (
            changing
            and request.url.path.startswith("/api/v1/resources")
            and request.headers.get("content-type", "").startswith(
                "multipart/form-data"
            )
        ):
            too_large = await _read_bounded_body(request, MAX_RESOURCE_REQUEST_BYTES)
            if too_large:
                return JSONResponse(
                    {"error": "Resource upload exceeds the 25 MiB limit."},
                    status_code=413,
                )
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
                "ui_locale": context.settings.ui_locale,
            },
        )

    @app.get("/api/v1/locales/{locale}")
    def locale_catalog(locale: str) -> JSONResponse:
        try:
            return JSONResponse(load_locale_catalog(locale))
        except LocaleCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

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
        payload = workspace_status(context, storage, include_credentials=False)
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
        resources = ResourceService(storage).list()
        packs = SourcePackService(service).list()
        return JSONResponse(
            {
                "active_generation_id": status.active_generation_id,
                "readiness": status.readiness,
                "is_partial": status.is_partial,
                "chunk_count": status.chunk_count,
                "embedding_ready_count": status.embedding_ready_count,
                "sources": service.source_statuses(),
                "source_packs": packs,
                "resources": resources,
                "resource_count": len(resources),
                "active_resource_count": sum(
                    bool(item["active"]) for item in resources
                ),
            }
        )

    @app.get("/api/v1/source-packs")
    def list_source_packs() -> JSONResponse:
        packs = SourcePackService(CorpusService(storage)).list()
        return JSONResponse({"source_packs": packs})

    @app.get("/api/v1/source-packs/{pack_id}")
    def get_source_pack(pack_id: str) -> JSONResponse:
        try:
            pack = SourcePackService(CorpusService(storage)).get(pack_id)
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(pack)

    @app.post("/api/v1/source-packs/{pack_id}/jobs")
    async def start_source_pack_job(pack_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "operation",
                "modules",
                "apply",
            }:
                raise ValueError("Source-pack job request contains unsupported fields.")
            operation = str(payload.get("operation", ""))
            if operation not in {"install", "update", "check", "remove", "restore"}:
                raise ValueError("Unsupported source-pack operation.")
            raw_modules = payload.get("modules")
            if raw_modules is not None and (
                not isinstance(raw_modules, list)
                or not all(isinstance(item, str) for item in raw_modules)
            ):
                raise ValueError("modules must be a list of source IDs.")
            packs = SourcePackService(CorpusService(storage))
            modules = packs.module_slugs(pack_id, raw_modules)
            if operation == "remove" and payload.get("apply") is not True:
                return JSONResponse(packs.removal_preview(pack_id, modules))
            job = maintenance_jobs.submit(
                operation,
                sources=modules,
                pack_id=pack_id,
                allow_partial=True,
            )
        except (CorpusValidationError, JobConflict, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(job_payload(job), status_code=202)

    @app.get("/api/v1/resources")
    def list_resources(include_removed: bool = True) -> JSONResponse:
        return JSONResponse(
            {
                "resources": ResourceService(storage).list(
                    include_removed=include_removed
                )
            }
        )

    @app.post("/api/v1/resources")
    async def add_resource(request: Request) -> JSONResponse:
        try:
            form = await request.form(
                max_files=1,
                max_fields=12,
                max_part_size=MAX_RESOURCE_REQUEST_BYTES,
            )
            upload = _form_upload(form)
            metadata = _resource_metadata_from_form(form, upload.filename)
            content = await upload.read(MAX_RESOURCE_BYTES + 1)
            await upload.close()
            if len(content) > MAX_RESOURCE_BYTES:
                return JSONResponse(
                    {"error": "Resource upload exceeds the 25 MiB limit."},
                    status_code=413,
                )
            job = resource_jobs.submit_add(
                content,
                filename=upload.filename or "resource",
                content_type=upload.content_type,
                metadata=metadata,
            )
        except JobConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (CorpusValidationError, KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(job_payload(job), status_code=202)

    @app.get("/api/v1/resources/{resource_id}")
    def get_resource(resource_id: str) -> JSONResponse:
        try:
            service = ResourceService(storage)
            resource = service.get(resource_id)
            resource["preview"] = service.preview_chunks(resource_id)
            return JSONResponse(resource)
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/v1/resources/{resource_id}/versions")
    async def replace_resource(resource_id: str, request: Request) -> JSONResponse:
        try:
            form = await request.form(
                max_files=1,
                max_fields=12,
                max_part_size=MAX_RESOURCE_REQUEST_BYTES,
            )
            upload = _form_upload(form)
            current = ResourceService(storage).get(resource_id)
            metadata = _resource_metadata_from_form(
                form,
                upload.filename,
                fallback=current,
            )
            content = await upload.read(MAX_RESOURCE_BYTES + 1)
            await upload.close()
            if len(content) > MAX_RESOURCE_BYTES:
                return JSONResponse(
                    {"error": "Resource upload exceeds the 25 MiB limit."},
                    status_code=413,
                )
            job = resource_jobs.submit_replace(
                resource_id,
                content,
                filename=upload.filename or "resource",
                content_type=upload.content_type,
                metadata=metadata,
                expected_version_id=_form_optional(form, "expected_version_id"),
            )
        except JobConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(job_payload(job), status_code=202)

    @app.patch("/api/v1/resources/{resource_id}")
    async def edit_resource(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "title",
                "publisher",
                "category",
                "jurisdiction",
                "original_url",
                "expected_version_id",
            }:
                raise ValueError("Resource update contains unsupported fields.")
            service = ResourceService(storage)
            current = service.get(resource_id)
            metadata = _resource_metadata_from_payload(payload, current)
            job, result = run_resource_mutation(
                storage,
                "resource_edit",
                lambda operation_id: service.edit_metadata(
                    resource_id,
                    metadata,
                    expected_version_id=(
                        str(payload["expected_version_id"])
                        if payload.get("expected_version_id")
                        else None
                    ),
                    operation_id=operation_id,
                ),
            )
            return JSONResponse({"job": job_payload(job), "result": result.as_dict()})
        except JobConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/v1/resources/{resource_id}/remove")
    def remove_resource(resource_id: str) -> JSONResponse:
        return _resource_mutation_response(
            storage,
            "resource_remove",
            lambda service, operation_id: service.remove(
                resource_id, operation_id=operation_id
            ),
        )

    @app.post("/api/v1/resources/{resource_id}/restore")
    async def restore_resource(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            version_id = (
                str(payload["version_id"])
                if isinstance(payload, dict) and payload.get("version_id")
                else None
            )
        except (AttributeError, TypeError, ValueError):
            return JSONResponse({"error": "Invalid restore request."}, status_code=400)
        return _resource_mutation_response(
            storage,
            "resource_restore",
            lambda service, operation_id: service.restore(
                resource_id,
                version_id=version_id,
                operation_id=operation_id,
            ),
        )

    @app.post("/api/v1/resources/{resource_id}/model-use")
    async def resource_model_use(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not isinstance(
                payload.get("allowed"), bool
            ):
                raise ValueError("allowed must be true or false.")
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return _resource_mutation_response(
            storage,
            "resource_model_use",
            lambda service, operation_id: service.set_model_use(
                resource_id,
                payload["allowed"],
                operation_id=operation_id,
            ),
        )

    @app.get("/api/v1/resources/{resource_id}/versions/{version_id}/chunks/{chunk_id}")
    def resource_excerpt(
        resource_id: str, version_id: str, chunk_id: str
    ) -> JSONResponse:
        try:
            return JSONResponse(
                ResourceService(storage).chunk(resource_id, version_id, chunk_id)
            )
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.get("/api/v1/resources/{resource_id}/versions/{version_id}/download")
    def download_resource(resource_id: str, version_id: str):
        try:
            service = ResourceService(storage)
            path = service.original_path(resource_id, version_id)
            provenance = service.version(resource_id, version_id)["provenance"]
            filename = str(provenance.get("original_basename") or "resource")
            media_type = str(provenance.get("media_type") or "application/octet-stream")
            return FileResponse(path, filename=filename, media_type=media_type)
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

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
                "offline",
                "property_cache_max_mb",
                "property_cache_retention_days",
                "operational_retention_days",
                "usage_retention_months",
                "ui_locale",
                "answer_language",
                "reading_style",
                "local_runtime_enabled",
                "local_runtime_endpoint",
            }
            if not isinstance(payload, dict) or not set(payload) <= allowed:
                raise LocalSettingsError("Unsupported or invalid settings field.")
            candidate = replace(context.settings, **payload).validate()
            save_local_settings(context.paths, candidate)
            context = replace(
                context,
                settings=candidate,
                network=network_policy_for_settings(candidate),
            )
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

    @app.get("/api/v1/credentials/{provider}")
    def provider_credential_status(provider: str) -> JSONResponse:
        if provider not in configured_credential_slots(context.settings):
            return JSONResponse(
                {
                    "error": (
                        "Credential slot is not used by a configured provider profile."
                    )
                },
                status_code=400,
            )
        try:
            result = asdict(CredentialResolver(context).presence(provider))
        except CredentialStoreError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse({"credential": result})

    @app.put("/api/v1/credentials/{provider}")
    async def replace_credential(provider: str, request: Request) -> JSONResponse:
        nonlocal context
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "Credential request must be JSON."}, status_code=400
            )
        try:
            result = _store_credential(context, provider, payload)
            context, activated = _activate_packaged_openai_profiles(context, provider)
            app.state.workspace = context
        except (AttributeError, CredentialStoreError, LocalSettingsError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result | {"profiles_activated": activated})

    @app.post("/api/v1/credentials")
    async def create_or_replace_credential(request: Request) -> JSONResponse:
        nonlocal context
        try:
            payload = await request.json()
            provider = str(payload.get("provider", ""))
        except Exception:
            return JSONResponse(
                {"error": "Credential request must be JSON."}, status_code=400
            )
        try:
            result = _store_credential(context, provider, payload)
            context, activated = _activate_packaged_openai_profiles(context, provider)
            app.state.workspace = context
        except (AttributeError, CredentialStoreError, LocalSettingsError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result | {"profiles_activated": activated})

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
        scope: str = "core",
        limit: int = 10,
    ) -> JSONResponse:
        try:
            origin = _scope_origin(scope)
            corpus = CorpusService(storage).status()
            result = LocalSearch(storage).search(
                q,
                filters=LocalSearchFilters(
                    source_slug=source,
                    source_type=source_type,
                    jurisdiction=jurisdiction,
                    origin=origin,
                ),
                limit=limit,
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        warnings = []
        pack_coverage = SourcePackService(CorpusService(storage)).coverage_for_query(q)
        if corpus.is_partial:
            warnings.append("The active legal corpus is partial.")
        if any(
            item["coverage_status"] == "partial_topic_coverage"
            for item in pack_coverage
        ):
            warnings.append(
                "Relevant optional source-pack coverage is incomplete; review "
                "the reported missing modules and limitations."
            )
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
                    "scope": scope,
                    "source_pack_notices": pack_coverage,
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
                        "origin": item.origin,
                        "category": item.category,
                        "model_use_allowed": item.model_use_allowed,
                        "source_version_id": item.source_version_id,
                        "content_hash": item.content_hash,
                        "locator": item.locator,
                        "jurisdiction": item.jurisdiction,
                        "source_url": item.source_url,
                        "publisher": item.publisher,
                        "retrieved_at": item.retrieved_at,
                        "last_checked_at": item.last_checked_at,
                        "effective_from": item.effective_from,
                        "effective_to": item.effective_to,
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
                "scope",
                "limit",
                "query_language",
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
            scope=str(payload.get("scope", "core")),
            limit=limit,
        )

    @app.post("/api/v1/query")
    async def start_answer(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "question",
                "source",
                "scope",
                "limit",
                "allow_unknown_cost",
                "answer_language",
                "reading_style",
            }:
                raise ValueError("Answer request contains unsupported fields.")
            if "allow_unknown_cost" in payload and not isinstance(
                payload["allow_unknown_cost"], bool
            ):
                raise ValueError("allow_unknown_cost must be true or false.")
            question = str(payload.get("question", ""))
            scope = str(payload.get("scope", "core"))
            origin = _scope_origin(scope)
            limit = int(payload.get("limit", 8))
            if not question.strip() or len(question) > 4_000 or not 1 <= limit <= 20:
                raise ValueError("Question or result limit is invalid.")
            job_id = interactive_jobs.submit(
                question,
                filters=LocalSearchFilters(
                    source_slug=payload.get("source"), origin=origin
                ),
                limit=limit,
                allow_unknown_cost=payload.get("allow_unknown_cost") is True,
                answer_language=(
                    str(payload["answer_language"])
                    if payload.get("answer_language")
                    else context.settings.answer_language
                ),
                reading_style=(
                    str(payload["reading_style"])
                    if payload.get("reading_style")
                    else context.settings.reading_style
                ),
            )
        except (AttributeError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        corpus = CorpusService(storage).status()
        answer_profile = get_configured_profile(context.settings, ProfileKind.ANSWER)
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
                    "scope": scope,
                },
                "warnings": warnings,
                "provenance": {
                    "generation_id": corpus.active_generation_id,
                    "answer_profile_id": answer_profile.id,
                    "cost_known": answer_profile.pricing_verified,
                },
                "output_preferences": {
                    "answer_language": payload.get(
                        "answer_language", context.settings.answer_language
                    ),
                    "reading_style": payload.get(
                        "reading_style", context.settings.reading_style
                    ),
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
                "language",
                "housing_context",
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
            suggestions = HelpResourceService(storage).match(
                question,
                language=str(payload.get("language", context.settings.ui_locale)),
                housing_context=payload.get("housing_context"),
            )
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
                            "resource_suggestions": suggestions,
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
                        "resource_suggestions": suggestions,
                    }
                )
            return JSONResponse(
                {
                    "mode": "search",
                    "route_reason": route.reason,
                    "query": None,
                    "message": "Searching installed legal sources locally.",
                    "resource_suggestions": suggestions,
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
            if job.job_type == "answer":
                payload = interactive_jobs.cancel(job_id)
            elif job.job_type.startswith("resource_"):
                payload = job_payload(resource_jobs.cancel(job_id))
            else:
                payload = job_payload(maintenance_jobs.cancel(job_id))
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
                else resource_jobs.resume(job_id)
                if existing.job_type in {"resource_add", "resource_replace"}
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
                (
                    []
                    if result.property_is_complete
                    else ["The summarized property page is not a complete history."]
                )
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

    # Durable research matters -------------------------------------------------
    @app.get("/api/v1/matters")
    def list_matters(
        q: str | None = None,
        include_archived: bool = False,
        limit: int = 100,
    ) -> JSONResponse:
        try:
            items = MatterService(storage).list(
                query=q, include_archived=include_archived, limit=limit
            )
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"matters": items})

    @app.post("/api/v1/matters")
    async def create_matter(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "title",
                "description",
                "tags",
            }:
                raise MatterError("Matter request contains unsupported fields.")
            matter = MatterService(storage).create(
                payload.get("title", ""),
                description=payload.get("description", ""),
                tags=payload.get("tags", []),
            )
        except (MatterError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(matter, status_code=201)

    @app.get("/api/v1/matters/{matter_id}")
    def get_matter(matter_id: str) -> JSONResponse:
        try:
            return JSONResponse(MatterService(storage).get(matter_id))
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.patch("/api/v1/matters/{matter_id}")
    async def update_matter(matter_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or "expected_revision" not in payload:
                raise MatterError("expected_revision is required.")
            revision = int(payload.pop("expected_revision"))
            result = MatterService(storage).update(
                matter_id, payload, expected_revision=revision
            )
        except MatterConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (MatterError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result)

    @app.delete("/api/v1/matters/{matter_id}")
    def delete_matter(matter_id: str, apply: bool = False) -> JSONResponse:
        try:
            return JSONResponse(MatterService(storage).delete(matter_id, apply=apply))
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/v1/matters/{matter_id}/items")
    async def save_matter_item(matter_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise MatterError("Saved-item request must be a JSON object.")
            allowed = {
                "job_id",
                "saved_item_id",
                "search",
                "dossier_id",
                "comparison_id",
                "idempotency_key",
            }
            if not set(payload) <= allowed:
                raise MatterError("Saved-item request contains unsupported fields.")
            service = MatterService(storage)
            if payload.get("saved_item_id"):
                return JSONResponse(
                    service.link(matter_id, str(payload["saved_item_id"]))
                )
            idempotency_key = str(payload.get("idempotency_key", ""))
            if payload.get("job_id"):
                job_id = str(payload["job_id"])
                job = interactive_jobs.get(job_id)
                if not job.get("result"):
                    raise MatterConflict("A completed server-held answer is required.")
                result = service.save_payload(
                    matter_id,
                    kind="answer",
                    payload=job["result"],
                    source_identity=f"answer-job:{job_id}",
                    idempotency_key=idempotency_key,
                    original_operation_id=job_id,
                )
            elif payload.get("search"):
                raw = payload["search"]
                if not isinstance(raw, dict) or not set(raw) <= {
                    "query",
                    "source",
                    "scope",
                    "limit",
                }:
                    raise MatterError("Saved search request is invalid.")
                query_text = str(raw.get("query", ""))
                scope = str(raw.get("scope", "core"))
                searched = LocalSearch(storage).search(
                    query_text,
                    filters=LocalSearchFilters(
                        source_slug=raw.get("source"), origin=_scope_origin(scope)
                    ),
                    limit=int(raw.get("limit", 10)),
                )
                saved_payload = {
                    "query": query_text,
                    "scope": scope,
                    "generation_id": searched.generation_id,
                    "method": searched.method,
                    "semantic_status": searched.semantic_status,
                    "results": [asdict(item) for item in searched.results],
                }
                identity = (
                    "search:"
                    + hashlib.sha256(
                        json.dumps(saved_payload, sort_keys=True).encode()
                    ).hexdigest()
                )
                result = service.save_payload(
                    matter_id,
                    kind="search",
                    payload=saved_payload,
                    source_identity=identity,
                    idempotency_key=idempotency_key,
                )
            elif payload.get("dossier_id"):
                dossier_id = str(payload["dossier_id"])
                dossier = PropertyDossierService(storage).get_observation(dossier_id)
                result = service.save_payload(
                    matter_id,
                    kind="property_dossier",
                    payload=dossier,
                    source_identity=f"dossier:{dossier_id}:{dossier['payload_hash']}",
                    idempotency_key=idempotency_key,
                )
            elif payload.get("comparison_id"):
                comparison_id = str(payload["comparison_id"])
                comparison = SourceComparisonService(storage).get(comparison_id)
                result = service.save_payload(
                    matter_id,
                    kind="comparison",
                    payload=comparison,
                    source_identity=f"comparison:{comparison_id}",
                    idempotency_key=idempotency_key,
                )
            else:
                raise MatterError("Choose a server-held result to save.")
        except JobNotFound:
            return JSONResponse({"error": "Answer job not found."}, status_code=404)
        except (MatterConflict, ComparisonError, DossierError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (MatterError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result, status_code=201)

    @app.get("/api/v1/saved-items/{item_id}")
    def get_saved_item(item_id: str) -> JSONResponse:
        try:
            return JSONResponse(MatterService(storage).get_item(item_id))
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.delete("/api/v1/saved-items/{item_id}")
    def delete_saved_item(
        item_id: str,
        matter_id: str | None = None,
        apply: bool = False,
    ) -> JSONResponse:
        try:
            return JSONResponse(
                MatterService(storage).delete_item(
                    item_id, matter_id=matter_id, apply=apply
                )
            )
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/v1/matters/{matter_id}/notes")
    async def create_matter_note(matter_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"body"}:
                raise MatterError("Note request requires only body.")
            note = MatterService(storage).put_note(
                matter_id=matter_id, body=payload["body"]
            )
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(note, status_code=201)

    @app.patch("/api/v1/notes/{note_id}")
    async def update_note(note_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {
                "body",
                "expected_revision",
                "matter_id",
            }:
                raise MatterError(
                    "Note update requires body, expected_revision, and matter_id."
                )
            note = MatterService(storage).put_note(
                note_id=note_id,
                matter_id=str(payload["matter_id"]),
                body=payload["body"],
                expected_revision=int(payload["expected_revision"]),
            )
        except MatterConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (MatterError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(note)

    @app.post("/api/v1/matters/{matter_id}/export")
    def export_matter(matter_id: str) -> JSONResponse:
        try:
            path = MatterService(storage).export(matter_id, context.paths.exports)
        except MatterError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(
            {"filename": path.name, "download_url": f"/api/v1/exports/{path.name}"}
        )

    # Retained source comparison ----------------------------------------------
    @app.post("/api/v1/comparisons")
    async def create_comparison(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {
                "module_slug",
                "baseline_version_id",
                "target_version_id",
            }:
                raise ComparisonError("Comparison request fields are invalid.")
            result = SourceComparisonService(storage).create(
                str(payload["module_slug"]),
                str(payload["baseline_version_id"]),
                str(payload["target_version_id"]),
            )
        except ComparisonError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result, status_code=201)

    @app.get("/api/v1/comparisons/{comparison_id}")
    def get_comparison(
        comparison_id: str,
        classification: str | None = None,
        include_unchanged: bool = False,
    ) -> JSONResponse:
        try:
            result = SourceComparisonService(storage).get(
                comparison_id,
                classification=classification,
                include_unchanged=include_unchanged,
            )
        except ComparisonError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(result)

    @app.post("/api/v1/comparisons/{comparison_id}/reviews")
    async def review_comparison(comparison_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {"item_id", "note"}:
                raise ComparisonError("Comparison review request is invalid.")
            result = SourceComparisonService(storage).record_review(
                comparison_id,
                str(payload.get("item_id", "")),
                note=str(payload.get("note", "")),
            )
        except ComparisonError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result, status_code=201)

    @app.post("/api/v1/comparisons/{comparison_id}/export")
    async def export_comparison(comparison_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            format = str(payload.get("format", "markdown"))
            path = SourceComparisonService(storage).export(
                comparison_id, context.paths.exports, format=format
            )
        except (AttributeError, ComparisonError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(
            {"filename": path.name, "download_url": f"/api/v1/exports/{path.name}"}
        )

    # Confirmed property dossiers ---------------------------------------------
    @app.post("/api/v1/properties/resolve")
    async def resolve_property(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("Property resolution request must be JSON.")
            confirmed = payload.pop("confirmed_building_id", None)
            refresh = bool(payload.pop("refresh", False))
            query = _property_query(payload)
            connector, repository = _property_repository(
                context, storage, property_connector_factory
            )
            try:
                result = repository.search(
                    query, refresh=refresh, deadline=Deadline.after(15)
                )
            finally:
                connector.close()
            resolved = PropertyDossierService(storage).resolve(
                result,
                confirmed_building_id=str(confirmed) if confirmed else None,
            )
        except (AttributeError, DossierError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except (
            CredentialStoreError,
            NetworkAccessDenied,
            OperationDeadlineExceeded,
            PropertyCacheError,
            PropertyConnectorError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse(resolved)

    @app.post("/api/v1/properties/{identity_id}/dossiers")
    async def create_dossier(identity_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "query",
                "panels",
                "refresh",
            }:
                raise DossierError("Dossier request contains unsupported fields.")
            query = _property_query(payload.get("query"))
            panels = payload.get("panels")
            if panels is not None and (
                not isinstance(panels, list)
                or not all(isinstance(item, str) for item in panels)
            ):
                raise DossierError("panels must be a list of names.")
            connector, repository = _property_repository(
                context, storage, property_connector_factory
            )
            try:
                result = repository.search(
                    query,
                    refresh=payload.get("refresh") is True,
                    deadline=Deadline.after(15),
                )
            finally:
                connector.close()
            dossier = PropertyDossierService(storage).create_observation(
                identity_id, result, panels=panels
            )
        except (AttributeError, DossierError, TypeError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except (
            CredentialStoreError,
            NetworkAccessDenied,
            OperationDeadlineExceeded,
            PropertyCacheError,
            PropertyConnectorError,
        ) as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        return JSONResponse(dossier, status_code=201)

    @app.get("/api/v1/dossiers/{dossier_id}")
    def get_dossier(dossier_id: str) -> JSONResponse:
        try:
            return JSONResponse(
                PropertyDossierService(storage).get_observation(dossier_id)
            )
        except DossierError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    @app.post("/api/v1/dossiers/{dossier_id}/export")
    def export_dossier(dossier_id: str) -> JSONResponse:
        try:
            path = PropertyDossierService(storage).export(
                dossier_id, context.paths.exports
            )
        except DossierError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        return JSONResponse(
            {"filename": path.name, "download_url": f"/api/v1/exports/{path.name}"}
        )

    # Rich extraction ----------------------------------------------------------
    @app.post("/api/v1/resources/{resource_id}/extraction-jobs")
    async def create_extraction_run(resource_id: str, request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "operation",
                "pages",
                "languages",
            }:
                raise CorpusValidationError(
                    "Extraction request contains unsupported fields."
                )
            pages = payload.get("pages")
            languages = payload.get("languages")
            if pages is not None and (
                not isinstance(pages, list)
                or not all(isinstance(item, int) for item in pages)
            ):
                raise CorpusValidationError(
                    "pages must be a list of physical page numbers."
                )
            if languages is not None and (
                not isinstance(languages, list)
                or not all(isinstance(item, str) for item in languages)
            ):
                raise CorpusValidationError(
                    "languages must be a list of language codes."
                )
            result = ExtractionService(storage).run(
                resource_id,
                operation=str(payload.get("operation", "inspect")),
                pages=pages,
                languages=languages,
            )
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(
            result, status_code=409 if result["status"] == "ocr_unavailable" else 201
        )

    @app.get("/api/v1/resources/{resource_id}/extraction-runs/{run_id}")
    def get_extraction_run(resource_id: str, run_id: str) -> JSONResponse:
        try:
            return JSONResponse(ExtractionService(storage).get(resource_id, run_id))
        except CorpusValidationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)

    # Offline-readable urgent help --------------------------------------------
    @app.get("/api/v1/help-resources")
    def list_help_resources(
        language: str = "en",
        topic: str | None = None,
        housing_context: str | None = None,
    ) -> JSONResponse:
        try:
            result = HelpResourceService(storage).list(
                language=language, topic=topic, housing_context=housing_context
            )
        except HelpCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(result)

    @app.post("/api/v1/help-resources/match")
    async def match_help_resources(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "question",
                "language",
                "housing_context",
            }:
                raise HelpCatalogError(
                    "Help match request contains unsupported fields."
                )
            result = HelpResourceService(storage).match(
                str(payload.get("question", "")),
                language=str(payload.get("language", "en")),
                housing_context=payload.get("housing_context"),
            )
        except HelpCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result)

    @app.post("/api/v1/help-resources/feedback")
    async def help_resource_feedback(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {
                "rule_ids",
                "topic",
                "helpful",
            }:
                raise HelpCatalogError("Help feedback request is invalid.")
            if not isinstance(payload["rule_ids"], list) or not isinstance(
                payload["helpful"], bool
            ):
                raise HelpCatalogError("Help feedback fields are invalid.")
            result = HelpResourceService(storage).feedback(
                rule_ids=payload["rule_ids"],
                topic=str(payload["topic"]),
                helpful=payload["helpful"],
            )
        except HelpCatalogError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(result, status_code=201)

    # Optional fully-offline extension boundary -------------------------------
    @app.get("/api/v1/offline/readiness")
    def offline_readiness() -> JSONResponse:
        try:
            return JSONResponse(OfflineExtensionService(context, storage).readiness())
        except OfflineExtensionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/v1/extensions/{extension_id}/jobs")
    async def offline_extension_job(
        extension_id: str, request: Request
    ) -> JSONResponse:
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "operation",
                "apply",
            }:
                raise OfflineExtensionError(
                    "Extension request contains unsupported fields."
                )
            operation = str(payload.get("operation", "check"))
            service = OfflineExtensionService(context, storage)
            if operation == "check":
                result = service.check(extension_id)
            elif operation == "install":
                result = service.install_unavailable(extension_id)
            elif operation == "remove":
                result = service.removal(
                    extension_id, apply=payload.get("apply") is True
                )
            else:
                raise OfflineExtensionError("Unsupported extension operation.")
        except OfflineExtensionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(
            result, status_code=409 if result.get("state") == "blocked" else 200
        )

    @app.get("/api/v1/data/snapshots")
    def list_data_snapshots() -> JSONResponse:
        return JSONResponse(
            {"snapshots": OfflineExtensionService(context, storage).snapshots()}
        )

    @app.post("/api/v1/data/analytics")
    async def data_analytics(request: Request) -> JSONResponse:
        # Consume and validate JSON even while the reviewed adapter is unavailable;
        # arbitrary SQL is never accepted.
        try:
            payload = await request.json()
            if not isinstance(payload, dict) or not set(payload) <= {
                "template",
                "parameters",
                "snapshot_id",
            }:
                raise OfflineExtensionError("Analytics request is invalid.")
        except (AttributeError, OfflineExtensionError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        return JSONResponse(
            OfflineExtensionService(context, storage).analytics_unavailable(),
            status_code=409,
        )

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


async def _read_bounded_body(request: Request, limit: int) -> bool:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > limit:
                return True
        except ValueError:
            pass
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            return True
    request._body = bytes(body)  # noqa: SLF001 - Starlette's cached request body.
    return False


def _form_upload(form: FormData) -> UploadFile:
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise ValueError("A PDF, Markdown, or UTF-8 text file is required.")
    return upload


def _form_optional(form: FormData, name: str) -> str | None:
    value = form.get(name)
    if value is None or isinstance(value, UploadFile):
        return None
    normalized = str(value).strip()
    return normalized or None


def _resource_metadata_from_form(
    form: FormData,
    filename: str | None,
    *,
    fallback: dict[str, object] | None = None,
) -> ResourceMetadata:
    prior = fallback or {}
    provenance = prior.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}

    def selected(name: str, default: str = "") -> str:
        supplied = _form_optional(form, name)
        if supplied is not None:
            return supplied
        return str(provenance.get(name) or default)

    title_default = str(
        provenance.get("title")
        or prior.get("name")
        or Path(filename or "resource").stem
    )
    allowed_value = _form_optional(form, "model_use_allowed")
    model_use_allowed = (
        allowed_value.lower() in {"1", "true", "yes", "on"}
        if allowed_value is not None
        else bool(prior.get("model_use_allowed", False))
    )
    return ResourceMetadata(
        title=selected("title", title_default),
        publisher=selected("publisher"),
        category=selected("category", "reference"),
        jurisdiction=selected("jurisdiction"),
        original_url=selected("original_url") or None,
        model_use_allowed=model_use_allowed,
    )


def _resource_metadata_from_payload(
    payload: dict[str, object], current: dict[str, object]
) -> ResourceMetadata:
    provenance = current.get("provenance")
    if not isinstance(provenance, dict):
        provenance = {}

    def selected(name: str, default: str = "") -> str:
        value = payload.get(name, provenance.get(name, default))
        return str(value or "")

    return ResourceMetadata(
        title=selected("title", str(current.get("name") or "Resource")),
        publisher=selected("publisher"),
        category=selected("category", "reference"),
        jurisdiction=selected("jurisdiction"),
        original_url=selected("original_url") or None,
        model_use_allowed=bool(current.get("model_use_allowed", False)),
    )


def _resource_mutation_response(storage, job_type: str, operation) -> JSONResponse:
    service = ResourceService(storage)
    try:
        job, result = run_resource_mutation(
            storage,
            job_type,
            lambda operation_id: operation(service, operation_id),
        )
    except JobConflict as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except CorpusValidationError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    return JSONResponse({"job": job_payload(job), "result": result.as_dict()})


def _scope_origin(scope: str) -> str | None:
    normalized = scope.strip().lower()
    if normalized in {"core", "official"}:
        return "core"
    if normalized in {"user", "mine"}:
        return "user"
    if normalized == "all":
        return None
    raise ValueError("Scope must be core, user, or all.")


def _security_error(message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def _store_credential(
    context: WorkspaceContext, provider: str, payload: object
) -> dict[str, object]:
    if provider not in configured_credential_slots(context.settings):
        raise CredentialStoreError(
            "Credential slot is not used by a configured provider profile."
        )
    if not isinstance(payload, dict) or not set(payload) <= {
        "provider",
        "credential",
        "storage",
    }:
        raise CredentialStoreError("Credential request contains unsupported fields.")
    value = validate_credential(provider, str(payload.get("credential", "")))
    backend = str(payload.get("storage", "keyring"))
    CredentialResolver(context).writable(backend).set(provider, value)
    return {
        "provider": provider,
        "present": True,
        "source": "keyring" if backend == "keyring" else "secret_file",
    }


def _activate_packaged_openai_profiles(
    context: WorkspaceContext, provider: str
) -> tuple[WorkspaceContext, bool]:
    if provider != "openai":
        return context, False
    updates: dict[str, str] = {}
    if get_profile(context.settings.answer_profile).provider == "fake":
        updates["answer_profile"] = "openai-answer-luna-v1"
    if get_profile(context.settings.embedding_profile).provider == "fake":
        updates["embedding_profile"] = "openai-embedding-3-small-v1"
    if not updates:
        return context, False
    settings = replace(context.settings, **updates).validate()
    save_local_settings(context.paths, settings)
    return replace(context, settings=settings), True


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
