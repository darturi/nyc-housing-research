from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import func, select

from app import __version__
from app.corpus.service import CorpusService
from app.credentials.store import (
    CredentialResolver,
    CredentialStoreError,
    configured_credential_slots,
)
from app.hpd.connector import load_hpd_manifest
from app.jobs.service import JobService
from app.providers.profiles import ProfileKind, get_configured_profile
from app.storage.database import LocalStorage
from app.storage.schema import (
    CORPUS_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    generations,
    maintenance_state,
    property_cache,
)
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext


def workspace_status(
    context: WorkspaceContext,
    storage: LocalStorage,
    *,
    include_credentials: bool = True,
) -> dict[str, object]:
    corpus = CorpusService(storage).status()
    with storage.corpus_engine.connect() as connection:
        retained = int(
            connection.scalar(
                select(func.count())
                .select_from(generations)
                .where(generations.c.status == "retained")
            )
            or 0
        )
        staging = int(
            connection.scalar(
                select(func.count())
                .select_from(generations)
                .where(generations.c.status == "staged")
            )
            or 0
        )
    with storage.state_engine.connect() as connection:
        cache = (
            connection.execute(
                select(
                    func.count().label("entries"),
                    func.coalesce(func.sum(property_cache.c.size_bytes), 0).label(
                        "bytes"
                    ),
                )
            )
            .mappings()
            .one()
        )
        maintenance = (
            connection.execute(
                select(maintenance_state).where(maintenance_state.c.id == 1)
            )
            .mappings()
            .one()
        )
    jobs = JobService(storage.state_engine).list()
    job_counts: dict[str, int] = {}
    for job in jobs:
        job_counts[job.state.value] = job_counts.get(job.state.value, 0) + 1
    usage = UsageLedger(storage).summary(
        monthly_cap_usd=Decimal(context.settings.monthly_budget_usd),
        timezone=context.settings.budget_timezone,
    )
    credentials: list[dict] = []
    credential_error = None
    if include_credentials:
        try:
            credentials = [
                asdict(CredentialResolver(context).presence(provider))
                for provider in configured_credential_slots(context.settings)
            ]
        except CredentialStoreError as exc:
            credential_error = str(exc)
    answer_profile = get_configured_profile(context.settings, ProfileKind.ANSWER)
    answer_slot = answer_profile.credential_slot or answer_profile.provider
    answer_credential_present = any(
        item["provider"] == answer_slot and item["present"] for item in credentials
    )
    return {
        "status": "initialized",
        "application_version": __version__,
        "workspace_id": context.settings.workspace_id,
        "data_dir": str(context.paths.root),
        "config_file": str(context.paths.config_file),
        "offline": context.settings.offline,
        "schema_versions": storage.versions(),
        "legal_corpus": {
            **asdict(corpus),
            "text_ready_count": corpus.chunk_count,
            "retained_generation_count": retained,
            "staging_generation_count": staging,
        },
        "profiles": {
            "answer": context.settings.answer_profile,
            "embedding": context.settings.embedding_profile,
        },
        "credentials": credentials,
        "credentials_checked": include_credentials,
        "credential_status_error": credential_error,
        "jobs": {"counts": job_counts, "reported_limit": 100},
        "property_cache": {
            "entry_count": int(cache["entries"]),
            "size_bytes": int(cache["bytes"]),
            "max_bytes": context.settings.property_cache_max_mb * 1024**2,
            "retention_days": context.settings.property_cache_retention_days,
        },
        "maintenance": {
            "active": bool(maintenance["active"]),
            "operation": maintenance["operation"],
            "started_at": (
                maintenance["started_at"].isoformat()
                if maintenance["started_at"]
                else None
            ),
        },
        "usage": {
            "month": usage.month,
            "cap_usd": str(usage.cap_usd),
            "settled_usd": str(usage.settled_usd),
            "reserved_usd": str(usage.reserved_usd),
            "uncertain_usd": str(usage.uncertain_usd),
            "remaining_usd": str(usage.remaining_usd),
            "unknown_cost_attempts": usage.unknown_cost_attempts,
            "unknown_cost_in_flight": usage.unknown_cost_in_flight,
            "unknown_cost_uncertain": usage.unknown_cost_uncertain,
            "scope": "this local installation only",
            "history_pruned_before": usage.history_pruned_before,
            "max_concurrent_paid_requests": (
                context.settings.max_concurrent_paid_requests
            ),
        },
        "retention": {
            "operational_days": context.settings.operational_retention_days,
            "property_cache_days": context.settings.property_cache_retention_days,
            "usage_months": context.settings.usage_retention_months,
        },
        "capabilities": {
            "keyless_search": bool(corpus.active_generation_id),
            "model_answers": bool(corpus.active_generation_id)
            and answer_profile.provider != "fake"
            and answer_profile.compatibility_verified
            and answer_credential_present,
            "synthetic_answer_demo": bool(corpus.active_generation_id),
            "property_lookup": "cache_only" if context.settings.offline else "live",
            "citywide_hpd_analytics": False,
        },
    }


def doctor_checks(
    context: WorkspaceContext,
    storage: LocalStorage | None,
    *,
    online: bool = False,
) -> dict[str, object]:
    nearest = context.paths.root
    while not nearest.exists() and nearest != nearest.parent:
        nearest = nearest.parent
    free_bytes = shutil.disk_usage(nearest).free
    checks: dict[str, dict[str, object]] = {
        "application_version": {"ok": True, "value": __version__},
        "python": {
            "ok": os.sys.version_info[:2] == (3, 12),
            "value": f"{os.sys.version_info.major}.{os.sys.version_info.minor}",
        },
        "sqlite_fts5": {"ok": _fts5_available()},
        "data_directory": {
            "ok": context.paths.root.exists(),
            "value": str(context.paths.root),
        },
        "disk_free_bytes": {"ok": free_bytes >= 2 * 1024**3, "value": free_bytes},
        "storage_location": {
            "ok": not _looks_cloud_synced(context.paths.root),
            "value": (
                "cloud-synced path detected; SQLite file locking may be unreliable"
                if _looks_cloud_synced(context.paths.root)
                else "no common cloud-sync path marker detected"
            ),
            "detection_limit": "heuristic path-name check only",
        },
        "offline_policy": {"ok": True, "value": context.settings.offline},
    }
    if storage is not None:
        versions = storage.versions()
        checks["schema_versions"] = {
            "ok": versions
            == {
                "corpus": CORPUS_SCHEMA_VERSION,
                "state": STATE_SCHEMA_VERSION,
            },
            "value": versions,
        }
        for name, path in (
            ("corpus_database", context.paths.corpus_database),
            ("state_database", context.paths.state_database),
        ):
            result = _quick_check(path)
            checks[name] = {"ok": result == "ok", "value": result}
        status = CorpusService(storage).status()
        checks["corpus_readiness"] = {
            "ok": bool(status.active_generation_id),
            "value": status.readiness,
        }
        selected_embedding = get_configured_profile(
            context.settings, ProfileKind.EMBEDDING
        )
        active_profile_id = None
        if status.active_generation_id:
            with storage.corpus_engine.connect() as connection:
                active_profile_id = connection.scalar(
                    select(generations.c.profile_id).where(
                        generations.c.id == status.active_generation_id
                    )
                )
        embedding_compatible = status.embedding_ready_count == 0 or (
            status.readiness == "hybrid_ready"
            and status.embedding_ready_count == status.chunk_count
            and active_profile_id == selected_embedding.id
        )
        checks["embedding_profile"] = {
            "ok": embedding_compatible,
            "value": {
                "selected": selected_embedding.id,
                "active_generation_profile": active_profile_id,
                "dimension": selected_embedding.dimension,
                "ready_chunks": status.embedding_ready_count,
                "total_chunks": status.chunk_count,
            },
        }
        if status.active_generation_id:
            try:
                verified = CorpusService(storage).verify_all_retained()
                checks["active_artifacts"] = {
                    "ok": True,
                    "value": {
                        "generation_count": verified["generation_count"],
                        "source_references": verified["verified_source_references"],
                    },
                }
            except Exception as exc:
                checks["active_artifacts"] = {"ok": False, "value": str(exc)}
        interrupted = [
            job.id
            for job in JobService(storage.state_engine).list()
            if job.state.value in {"paused", "failed", "cancel_requested"}
        ]
        checks["interrupted_jobs"] = {
            "ok": not interrupted,
            "value": len(interrupted),
        }
        with storage.state_engine.connect() as connection:
            cache = (
                connection.execute(
                    select(
                        func.count().label("entries"),
                        func.coalesce(func.sum(property_cache.c.size_bytes), 0).label(
                            "bytes"
                        ),
                    )
                )
                .mappings()
                .one()
            )
        checks["property_cache"] = {
            "ok": int(cache["bytes"])
            <= context.settings.property_cache_max_mb * 1024**2,
            "value": {
                "entries": int(cache["entries"]),
                "bytes": int(cache["bytes"]),
                "configured_max_bytes": context.settings.property_cache_max_mb
                * 1024**2,
            },
        }
        try:
            presence = {
                provider: asdict(CredentialResolver(context).presence(provider))
                for provider in configured_credential_slots(context.settings)
            }
            checks["credential_presence"] = {"ok": True, "value": presence}
        except CredentialStoreError as exc:
            checks["credential_presence"] = {
                "ok": False,
                "value": type(exc).__name__,
            }
    if online:
        checks["hpd_public_api"] = _hpd_online_check(context)
    ok = all(bool(check["ok"]) for check in checks.values())
    return {
        "status": "ok" if ok else "attention",
        "read_only": True,
        "online_checks_requested": online,
        "paid_checks_performed": False,
        "checks": checks,
    }


def export_redacted_diagnostics(
    context: WorkspaceContext,
    storage: LocalStorage | None,
    destination: Path,
    *,
    replace_existing: bool = False,
) -> dict[str, object]:
    destination = destination.expanduser().resolve()
    if destination.exists() and not replace_existing:
        raise ValueError(
            "Diagnostic destination already exists; choose another path or "
            "explicitly allow replacement."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "format_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "network_checks_performed": False,
        "paid_checks_performed": False,
        "redactions": [
            "credentials and session/bootstrap values excluded",
            "questions, addresses, prompts, and provider bodies excluded",
            "workspace and home-directory paths replaced",
        ],
        "doctor": doctor_checks(context, storage, online=False),
        "status": (
            workspace_status(context, storage)
            if storage is not None
            else {
                "status": "not_initialized",
                "application_version": __version__,
                "schema_versions": {"corpus": None, "state": None},
            }
        ),
    }
    redacted = _redact_paths(
        report,
        (
            (str(context.paths.root.resolve()), "<workspace>"),
            (str(Path.home().resolve()), "<home>"),
        ),
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(redacted, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "exported",
        "path": str(destination),
        "format_version": 1,
        "network_checks_performed": False,
        "paid_checks_performed": False,
    }


def _redact_paths(value, replacements: tuple[tuple[str, str], ...]):
    if isinstance(value, dict):
        return {
            key: _redact_paths(item, replacements) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_paths(item, replacements) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_paths(item, replacements) for item in value)
    if isinstance(value, str):
        redacted = value
        for original, replacement in replacements:
            if original:
                redacted = redacted.replace(original, replacement)
        return redacted
    return value


def _quick_check(path) -> str:
    try:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
        return str(row[0]) if row else "no result"
    except sqlite3.Error as exc:
        return f"error: {exc}"


def _fts5_available() -> bool:
    with sqlite3.connect(":memory:") as connection:
        try:
            connection.execute("CREATE VIRTUAL TABLE fts5_probe USING fts5(body)")
        except sqlite3.OperationalError:
            return False
    return True


def _looks_cloud_synced(path) -> bool:
    markers = {"dropbox", "onedrive", "icloud drive", "google drive"}
    return any(part.lower() in markers for part in path.parts)


def _hpd_online_check(context: WorkspaceContext) -> dict[str, object]:
    manifest = load_hpd_manifest()
    endpoint = manifest["endpoint"]
    try:
        context.network.assert_url_allowed(endpoint, purpose="doctor HPD check")
        token, _source = CredentialResolver(context).resolve("socrata")
        headers = {"Accept": "application/json"}
        if token:
            headers["X-App-Token"] = token
        with httpx.Client(timeout=10.0) as client:
            response = client.get(
                endpoint,
                params={"$select": "violationid", "$limit": "1"},
                headers=headers,
            )
            response.raise_for_status()
            payload = response.json()
        valid = (
            isinstance(payload, list)
            and len(payload) <= 1
            and (not payload or str(payload[0].get("violationid", "")).isdigit())
        )
        return {
            "ok": valid,
            "value": "bounded public schema check",
            "dataset_id": manifest["dataset_id"],
        }
    except Exception as exc:
        return {"ok": False, "value": f"unavailable: {type(exc).__name__}"}
