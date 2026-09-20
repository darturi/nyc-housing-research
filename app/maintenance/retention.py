from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select, update

from app.maintenance.barrier import MaintenanceBarrier
from app.storage.database import LocalStorage
from app.storage.schema import (
    application_settings,
    jobs,
    property_cache,
    usage_events,
)
from app.workspace.settings import LocalSettings

USAGE_RETENTION_KEY = "usage_retention_history"
ACTIVE_JOB_STATES = ("queued", "running", "cancel_requested")
TERMINAL_JOB_STATES = ("succeeded", "failed", "cancelled")


class RetentionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetentionResult:
    applied: bool
    generated_at: str
    operational_cutoff: str
    property_cache_cutoff: str
    usage_cutoff: str
    candidate_jobs: int
    candidate_usage_attempts: int
    candidate_usage_events: int
    candidate_cache_entries: int
    candidate_cache_artifacts: int
    candidate_log_files: int
    candidate_file_bytes: int
    candidate_staging_files: int = 0
    removed_staging_files: int = 0
    removed_jobs: int = 0
    removed_usage_attempts: int = 0
    removed_usage_events: int = 0
    removed_cache_entries: int = 0
    removed_cache_artifacts: int = 0
    removed_log_files: int = 0
    removed_file_bytes: int = 0
    protected_active_jobs: int = 0
    protected_unresolved_usage_attempts: int = 0
    protected_current_month_usage_attempts: int = 0
    protected_pinned_cache_entries: int = 0
    file_delete_failures: int = 0
    usage_history_pruned_before: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class _RetentionPlan:
    now: datetime
    operational_cutoff: datetime
    property_cache_cutoff: datetime
    usage_cutoff: datetime
    job_ids: tuple[str, ...]
    usage_attempt_ids: tuple[str, ...]
    usage_event_count: int
    cache_keys: tuple[str, ...]
    cache_artifacts: tuple[Path, ...]
    log_files: tuple[Path, ...]
    staging_files: tuple[Path, ...]
    candidate_file_bytes: int
    protected_active_jobs: int
    protected_unresolved_usage_attempts: int
    protected_current_month_usage_attempts: int
    protected_pinned_cache_entries: int


class WorkspaceRetentionService:
    """Conservatively prune bounded local metadata and cache artifacts."""

    def __init__(self, storage: LocalStorage, settings: LocalSettings) -> None:
        self._storage = storage
        self._settings = settings
        self._barrier = MaintenanceBarrier(storage.state_engine)

    def run(
        self, *, apply: bool = False, now: datetime | None = None
    ) -> RetentionResult:
        now = _aware(now or datetime.now(UTC))
        if not apply:
            return self._result(self._plan(now), applied=False)

        self._enter_barrier(now)
        try:
            plan = self._plan(now)
            removed = self._delete_database_rows(plan)
            self._checkpoint_state_wal()
            file_results = self._delete_files(plan)
            return self._result(
                plan,
                applied=True,
                removed=removed,
                file_results=file_results,
            )
        finally:
            self._leave_barrier()

    def _plan(self, now: datetime) -> _RetentionPlan:
        operational_cutoff = now - timedelta(
            days=self._settings.operational_retention_days
        )
        property_cutoff = now - timedelta(
            days=self._settings.property_cache_retention_days
        )
        usage_cutoff = _calendar_month_cutoff(
            now,
            self._settings.budget_timezone,
            self._settings.usage_retention_months,
        )
        with self._storage.state_engine.connect() as connection:
            job_rows = list(connection.execute(select(jobs)).mappings())
            cache_rows = list(connection.execute(select(property_cache)).mappings())
            usage_rows = list(
                connection.execute(
                    select(usage_events).order_by(
                        usage_events.c.created_at, usage_events.c.id
                    )
                ).mappings()
            )

        job_ids = tuple(
            row["id"]
            for row in job_rows
            if row["state"] in TERMINAL_JOB_STATES
            and not (row["state"] == "failed" and row["retryable"])
            and _aware(row["updated_at"]) < operational_cutoff
        )
        protected_active_jobs = sum(
            row["state"] not in TERMINAL_JOB_STATES for row in job_rows
        )

        candidate_cache_rows = [
            row
            for row in cache_rows
            if not row["pinned"] and _aware(row["fetched_at"]) < property_cutoff
        ]
        candidate_cache_keys = {row["cache_key"] for row in candidate_cache_rows}
        protected_pinned_cache_entries = sum(bool(row["pinned"]) for row in cache_rows)
        references: dict[str, set[str]] = {}
        for row in cache_rows:
            references.setdefault(row["response_artifact"], set()).add(row["cache_key"])
        cache_artifacts = []
        for relative, keys in references.items():
            if keys and keys <= candidate_cache_keys:
                path = self._safe_cache_artifact(relative)
                if path is not None:
                    cache_artifacts.append(path)

        attempts: dict[str, list[dict]] = {}
        for row in usage_rows:
            attempts.setdefault(row["attempt_id"], []).append(dict(row))
        usage_attempt_ids = []
        protected_unresolved = 0
        protected_current = 0
        current_month = _budget_month(now, self._settings.budget_timezone)
        for attempt_id, rows in attempts.items():
            reserve = next(
                (row for row in rows if row["event_type"] == "reserve"), None
            )
            event_types = {row["event_type"] for row in rows}
            final_status = (
                "settled"
                if "correction" in event_types or "settle" in event_types
                else "uncertain"
                if "uncertain" in event_types
                else "reserved"
            )
            if reserve is None or final_status in {"reserved", "uncertain"}:
                protected_unresolved += 1
                continue
            if (
                _budget_month(
                    _aware(reserve["created_at"]), self._settings.budget_timezone
                )
                == current_month
            ):
                protected_current += 1
                continue
            if max(_aware(row["created_at"]) for row in rows) < usage_cutoff:
                usage_attempt_ids.append(attempt_id)
        usage_attempt_set = set(usage_attempt_ids)
        usage_event_count = sum(
            row["attempt_id"] in usage_attempt_set for row in usage_rows
        )

        log_files = tuple(self._old_log_files(operational_cutoff))
        protected_stages = {
            json.loads(row["resume_json"]).get("stage_id")
            for row in job_rows
            if row["state"] not in TERMINAL_JOB_STATES
            or (row["state"] == "failed" and row["retryable"])
        }
        stage_root = self._storage.paths.artifacts / "resource-staging"
        staging_files = tuple(
            path
            for path in stage_root.glob("*")
            if path.suffix in {".bin", ".json"}
            and path.stem not in protected_stages
            and not path.is_symlink()
            and path.is_file()
            and datetime.fromtimestamp(path.stat().st_mtime, UTC) < operational_cutoff
        )
        file_paths = set(cache_artifacts) | set(log_files) | set(staging_files)
        candidate_file_bytes = sum(_regular_file_size(path) for path in file_paths)
        return _RetentionPlan(
            now=now,
            operational_cutoff=operational_cutoff,
            property_cache_cutoff=property_cutoff,
            usage_cutoff=usage_cutoff,
            job_ids=job_ids,
            usage_attempt_ids=tuple(usage_attempt_ids),
            usage_event_count=usage_event_count,
            cache_keys=tuple(candidate_cache_keys),
            cache_artifacts=tuple(cache_artifacts),
            log_files=log_files,
            staging_files=staging_files,
            candidate_file_bytes=candidate_file_bytes,
            protected_active_jobs=protected_active_jobs,
            protected_unresolved_usage_attempts=protected_unresolved,
            protected_current_month_usage_attempts=protected_current,
            protected_pinned_cache_entries=protected_pinned_cache_entries,
        )

    def _delete_database_rows(self, plan: _RetentionPlan) -> dict[str, int]:
        removed = {"jobs": 0, "usage_events": 0, "cache_entries": 0}
        with self._storage.state_engine.begin() as connection:
            if plan.job_ids:
                result = connection.execute(
                    delete(jobs).where(jobs.c.id.in_(plan.job_ids))
                )
                removed["jobs"] = int(result.rowcount or 0)
            if plan.usage_attempt_ids:
                result = connection.execute(
                    delete(usage_events).where(
                        usage_events.c.attempt_id.in_(plan.usage_attempt_ids)
                    )
                )
                removed["usage_events"] = int(result.rowcount or 0)
                self._record_usage_floor(connection, plan)
            if plan.cache_keys:
                result = connection.execute(
                    delete(property_cache).where(
                        property_cache.c.cache_key.in_(plan.cache_keys)
                    )
                )
                removed["cache_entries"] = int(result.rowcount or 0)
        return removed

    def _record_usage_floor(self, connection, plan: _RetentionPlan) -> None:
        existing = connection.scalar(
            select(application_settings.c.value_json).where(
                application_settings.c.key == USAGE_RETENTION_KEY
            )
        )
        previous_floor = None
        if existing:
            try:
                previous_floor = json.loads(existing).get("history_pruned_before")
            except (AttributeError, json.JSONDecodeError, TypeError):
                previous_floor = None
        floor = max(previous_floor or "", plan.usage_cutoff.isoformat())
        value = json.dumps(
            {
                "history_pruned_before": floor,
                "last_pruned_at": plan.now.isoformat(),
            },
            sort_keys=True,
        )
        result = connection.execute(
            update(application_settings)
            .where(application_settings.c.key == USAGE_RETENTION_KEY)
            .values(value_json=value, updated_at=plan.now)
        )
        if not result.rowcount:
            connection.execute(
                insert(application_settings).values(
                    key=USAGE_RETENTION_KEY,
                    value_json=value,
                    updated_at=plan.now,
                )
            )

    def _delete_files(self, plan: _RetentionPlan) -> dict[str, int]:
        result = {
            "cache_artifacts": 0,
            "log_files": 0,
            "staging_files": 0,
            "file_bytes": 0,
            "failures": 0,
        }
        for kind, paths in (
            ("cache_artifacts", plan.cache_artifacts),
            ("log_files", plan.log_files),
            ("staging_files", plan.staging_files),
        ):
            for path in paths:
                size = _regular_file_size(path)
                if not size and not path.exists():
                    continue
                try:
                    if path.is_symlink() or not path.is_file():
                        continue
                    path.unlink()
                    result[kind] += 1
                    result["file_bytes"] += size
                except OSError:
                    result["failures"] += 1
        return result

    def _checkpoint_state_wal(self) -> None:
        connection = self._storage.state_engine.raw_connection()
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        finally:
            connection.close()

    def _old_log_files(self, cutoff: datetime):
        root = self._storage.paths.logs
        if not root.is_dir():
            return
        for current, directories, filenames in os.walk(root, followlinks=False):
            directories[:] = [
                name for name in directories if not (Path(current) / name).is_symlink()
            ]
            for filename in filenames:
                path = Path(current) / filename
                try:
                    metadata = path.lstat()
                except OSError:
                    continue
                if (
                    stat.S_ISREG(metadata.st_mode)
                    and datetime.fromtimestamp(metadata.st_mtime, UTC) < cutoff
                ):
                    yield path

    def _safe_cache_artifact(self, relative: str) -> Path | None:
        relative_path = Path(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            return None
        path = self._storage.paths.root / relative_path
        root = (self._storage.paths.artifacts / "property-cache").resolve()
        try:
            path.parent.resolve().relative_to(root)
        except ValueError:
            return None
        return path

    def _enter_barrier(self, now: datetime) -> None:
        try:
            self._barrier.enter("retention", now)
        except RuntimeError as exc:
            raise RetentionError(str(exc)) from exc

    def _leave_barrier(self) -> None:
        self._barrier.leave()

    @staticmethod
    def _result(
        plan: _RetentionPlan,
        *,
        applied: bool,
        removed: dict[str, int] | None = None,
        file_results: dict[str, int] | None = None,
    ) -> RetentionResult:
        removed = removed or {}
        file_results = file_results or {}
        return RetentionResult(
            applied=applied,
            generated_at=plan.now.isoformat(),
            operational_cutoff=plan.operational_cutoff.isoformat(),
            property_cache_cutoff=plan.property_cache_cutoff.isoformat(),
            usage_cutoff=plan.usage_cutoff.isoformat(),
            candidate_jobs=len(plan.job_ids),
            candidate_usage_attempts=len(plan.usage_attempt_ids),
            candidate_usage_events=plan.usage_event_count,
            candidate_cache_entries=len(plan.cache_keys),
            candidate_cache_artifacts=len(plan.cache_artifacts),
            candidate_log_files=len(plan.log_files),
            candidate_file_bytes=plan.candidate_file_bytes,
            candidate_staging_files=len(plan.staging_files),
            removed_staging_files=file_results.get("staging_files", 0),
            removed_jobs=removed.get("jobs", 0),
            removed_usage_attempts=(
                len(plan.usage_attempt_ids) if removed.get("usage_events", 0) else 0
            ),
            removed_usage_events=removed.get("usage_events", 0),
            removed_cache_entries=removed.get("cache_entries", 0),
            removed_cache_artifacts=file_results.get("cache_artifacts", 0),
            removed_log_files=file_results.get("log_files", 0),
            removed_file_bytes=file_results.get("file_bytes", 0),
            protected_active_jobs=plan.protected_active_jobs,
            protected_unresolved_usage_attempts=(
                plan.protected_unresolved_usage_attempts
            ),
            protected_current_month_usage_attempts=(
                plan.protected_current_month_usage_attempts
            ),
            protected_pinned_cache_entries=plan.protected_pinned_cache_entries,
            file_delete_failures=file_results.get("failures", 0),
            usage_history_pruned_before=(
                plan.usage_cutoff.isoformat()
                if applied and removed.get("usage_events", 0)
                else None
            ),
        )


def usage_history_pruned_before(storage: LocalStorage) -> str | None:
    with storage.state_engine.connect() as connection:
        value = connection.scalar(
            select(application_settings.c.value_json).where(
                application_settings.c.key == USAGE_RETENTION_KEY
            )
        )
    if not value:
        return None
    try:
        result = json.loads(value).get("history_pruned_before")
    except (AttributeError, json.JSONDecodeError, TypeError):
        return None
    return result if isinstance(result, str) else None


def _calendar_month_cutoff(now: datetime, timezone: str, months: int) -> datetime:
    local = now.astimezone(ZoneInfo(timezone))
    month_index = local.year * 12 + local.month - 1 - (months - 1)
    year, zero_based_month = divmod(month_index, 12)
    local_cutoff = datetime(year, zero_based_month + 1, 1, tzinfo=local.tzinfo)
    return local_cutoff.astimezone(UTC)


def _budget_month(value: datetime, timezone: str) -> str:
    return value.astimezone(ZoneInfo(timezone)).strftime("%Y-%m")


def _regular_file_size(path: Path) -> int:
    try:
        metadata = path.lstat()
    except OSError:
        return 0
    return metadata.st_size if stat.S_ISREG(metadata.st_mode) else 0


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
