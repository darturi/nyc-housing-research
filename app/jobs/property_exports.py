from __future__ import annotations

import hashlib
import json
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict

from app.credentials.store import CredentialResolver
from app.exporting.service import ResearchExporter
from app.hpd.cache import CachedPropertyRepository
from app.hpd.complete import export_complete_property_result
from app.hpd.connector import HpdSocrataConnector, PropertyQuery
from app.jobs.runtime import Deadline, OperationCancelled
from app.jobs.service import (
    InvalidJobTransition,
    JobRecord,
    JobService,
    JobState,
)
from app.maintenance.logging import LocalDiagnosticLog
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


class PropertyExportJobs:
    """Run bounded complete-property exports outside HTTP request threads."""

    def __init__(
        self,
        context: WorkspaceContext,
        storage: LocalStorage,
        *,
        connector_factory=None,
    ) -> None:
        self._base_context = context
        self._jobs = JobService(storage.state_engine)
        self._jobs.recover_interrupted()
        self._connector_factory = connector_factory
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="property-export",
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    @property
    def _context(self) -> WorkspaceContext:
        return self._base_context.current()

    def submit(
        self,
        query: PropertyQuery,
        *,
        max_pages: int = 20,
        deadline_seconds: float = 120,
    ) -> JobRecord:
        query.validate()
        if query.continuation:
            raise ValueError("Complete export must start at the first page.")
        if not 1 <= max_pages <= 100:
            raise ValueError("Complete export max pages must be between 1 and 100.")
        if not 1 <= deadline_seconds <= 900:
            raise ValueError(
                "Complete export deadline must be between 1 and 900 seconds."
            )
        query_payload = asdict(query)
        identity = hashlib.sha256(
            json.dumps(query_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        resume = {
            "query": query_payload,
            "max_pages": max_pages,
            "deadline_seconds": deadline_seconds,
        }
        job = self._jobs.create(
            "property_complete_export",
            f"property-export:{identity}",
            resume=resume,
        )
        self._schedule(job.id)
        return job

    def resume(self, job_id: str) -> JobRecord:
        existing = self._jobs.get(job_id)
        if existing.job_type != "property_complete_export":
            raise InvalidJobTransition(
                "Only a complete-property export can use this runner."
            )
        with self._lock:
            previous = self._futures.get(job_id)
        if previous is not None:
            previous.result()
        resumed = self._jobs.resume(job_id)
        self._schedule(job_id)
        return resumed

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def wait(self, job_id: str) -> JobRecord:
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result()
        return self._jobs.get(job_id)

    def _schedule(self, job_id: str) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                raise InvalidJobTransition("The job already has an active worker.")
            self._futures[job_id] = self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        worker_id = f"property-web-{uuid.uuid4()}"
        storage = LocalStorage.open(self._context.paths)
        jobs = JobService(storage.state_engine)
        connector = None
        try:
            record = jobs.claim(job_id, worker_id, lease_seconds=300)
            payload = record.resume
            query_payload = payload.get("query")
            if not isinstance(query_payload, dict):
                raise ValueError("Property export query state is missing.")
            query = PropertyQuery(**query_payload)
            max_pages = int(payload.get("max_pages", 20))
            deadline_seconds = float(payload.get("deadline_seconds", 120))
            credential, _source = CredentialResolver(self._context).resolve("socrata")
            connector = (
                self._connector_factory(self._context, credential)
                if self._connector_factory
                else HpdSocrataConnector(
                    self._context.network,
                    app_token=credential,
                )
            )
            repository = CachedPropertyRepository(
                storage,
                connector,
                max_bytes=self._context.settings.property_cache_max_mb * 1024**2,
                retention_days=self._context.settings.property_cache_retention_days,
            )
            export_complete_property_result(
                repository,
                ResearchExporter(self._context.paths),
                jobs,
                query,
                max_pages=max_pages,
                deadline=Deadline.after(deadline_seconds),
                cancellation=_JobCancellation(jobs, job_id),
                job_id=job_id,
                worker_id=worker_id,
                resume=payload,
            )
        except OperationCancelled:
            # The shared export service records the terminal cancelled state.
            pass
        except Exception as exc:
            LocalDiagnosticLog(self._context.paths.logs).record(
                "property_export_job_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
            try:
                state = jobs.get(job_id).state
                if state == JobState.CANCEL_REQUESTED:
                    jobs.finish_cancel(job_id, worker_id)
                elif state == JobState.RUNNING:
                    jobs.fail(
                        job_id,
                        worker_id,
                        error_code="property_complete_export_failed",
                        error_message=str(exc),
                        retryable=True,
                    )
            except Exception:
                pass
        finally:
            if connector is not None:
                connector.close()
            storage.close()


class _JobCancellation:
    def __init__(self, jobs: JobService, job_id: str) -> None:
        self._jobs = jobs
        self._job_id = job_id

    def raise_if_cancelled(self) -> None:
        if self._jobs.get(self._job_id).state == JobState.CANCEL_REQUESTED:
            raise OperationCancelled("Complete property export was cancelled.")
