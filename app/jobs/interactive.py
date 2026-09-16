from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from app.answer.local import LocalAnswerEvidence, LocalAnswerService
from app.credentials.store import CredentialResolver
from app.jobs.runtime import CancellationSignal, Deadline, OperationCancelled
from app.jobs.service import JobService, JobState
from app.maintenance.logging import LocalDiagnosticLog
from app.providers.gateway import ProviderGateway
from app.retrieval.local import LocalSearchFilters
from app.storage.database import LocalStorage
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext


@dataclass
class InteractiveRecord:
    job_id: str
    stage: str
    evidence: list[dict]
    result: dict | None
    error: str | None
    expires_at: datetime


class InteractiveAnswerJobs:
    """Process-local payload/result storage backed by persistent metadata only."""

    def __init__(
        self,
        context: WorkspaceContext,
        storage: LocalStorage,
        *,
        expiry_minutes: int = 30,
    ) -> None:
        self._context = context
        self._storage = storage
        self._jobs = JobService(storage.state_engine)
        self._jobs.recover_interrupted()
        self._records: dict[str, InteractiveRecord] = {}
        self._signals: dict[str, CancellationSignal] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="answer")
        self._expiry = timedelta(minutes=expiry_minutes)

    def submit(
        self,
        question: str,
        *,
        filters: LocalSearchFilters | None = None,
        limit: int = 8,
        deadline_seconds: float | None = None,
        allow_unknown_cost: bool = False,
    ) -> str:
        deadline_seconds = (
            deadline_seconds
            if deadline_seconds is not None
            else self._context.settings.answer_deadline_seconds
        )
        job = self._jobs.create("answer", f"interactive:{uuid.uuid4()}")
        signal = CancellationSignal()
        with self._lock:
            self._records[job.id] = InteractiveRecord(
                job_id=job.id,
                stage="queued",
                evidence=[],
                result=None,
                error=None,
                expires_at=datetime.now(UTC) + self._expiry,
            )
            self._signals[job.id] = signal
        self._executor.submit(
            self._run,
            job.id,
            question,
            filters,
            limit,
            deadline_seconds,
            signal,
            allow_unknown_cost,
        )
        return job.id

    def get(self, job_id: str) -> dict:
        self._prune()
        job = self._jobs.get(job_id)
        with self._lock:
            record = self._records.get(job_id)
            result = record.result if record else None
            expired = record is None and job.state == JobState.SUCCEEDED
            resubmit_required = record is None and job.state in {
                JobState.FAILED,
                JobState.SUCCEEDED,
            }
            evidence = list(record.evidence) if record else []
            return {
                "job_id": job.id,
                "status": (
                    "expired_result"
                    if expired
                    else "resubmit_required"
                    if resubmit_required
                    else result.get("status", job.state.value)
                    if result
                    else job.state.value
                ),
                "mode": "answer",
                "state": job.state.value,
                "stage": record.stage if record else job.stage,
                "coverage": {"evidence_count": len(evidence)},
                "warnings": (
                    ["The in-memory result expired; submit the question again."]
                    if expired
                    else []
                ),
                "provenance": {
                    "operation_id": result.get("operation_id") if result else None,
                    "generation_id": result.get("generation_id") if result else None,
                    "answer_profile_id": (
                        result.get("answer_profile_id") if result else None
                    ),
                    "embedding_profile_id": (
                        result.get("embedding_profile_id") if result else None
                    ),
                    "retrieval_method": (
                        result.get("retrieval_method") if result else None
                    ),
                    "prompt_version": (
                        result.get("prompt_version") if result else None
                    ),
                },
                "evidence": evidence,
                "result": result,
                "error": record.error if record else job.error_message,
                "expires_at": record.expires_at.isoformat() if record else None,
                "resubmit_required": resubmit_required,
            }

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.request_cancel(job_id)
        with self._lock:
            signal = self._signals.get(job_id)
            if signal:
                signal.cancel()
        return {"job_id": job.id, "state": job.state.value}

    def close(self) -> None:
        with self._lock:
            for signal in self._signals.values():
                signal.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(
        self,
        job_id,
        question,
        filters,
        limit,
        deadline_seconds,
        signal,
        allow_unknown_cost,
    ):
        worker_id = f"web-{uuid.uuid4()}"
        gateway = ProviderGateway(self._context, UsageLedger(self._storage))
        try:
            self._jobs.claim(
                job_id, worker_id, lease_seconds=max(60, int(deadline_seconds) + 5)
            )
            self._set_stage(job_id, "retrieving")

            def evidence_ready(
                evidence: tuple[LocalAnswerEvidence, ...], generation_id: str
            ) -> None:
                with self._lock:
                    record = self._records.get(job_id)
                    if record:
                        record.evidence = [asdict(item) for item in evidence]
                        record.stage = "answering"
                self._jobs.checkpoint(
                    job_id,
                    worker_id,
                    stage="answering",
                    current=1,
                    total=2,
                    resume={"generation_id": generation_id},
                )

            result = LocalAnswerService(
                self._context,
                self._storage,
                gateway,
                CredentialResolver(self._context),
            ).answer(
                question,
                filters=filters,
                limit=limit,
                deadline=Deadline.after(deadline_seconds),
                cancellation=signal,
                operation_id=job_id,
                on_evidence=evidence_ready,
                allow_unknown_cost=allow_unknown_cost,
            )
            with self._lock:
                record = self._records.get(job_id)
                if record:
                    record.result = asdict(result)
                    record.stage = "complete"
                    record.expires_at = datetime.now(UTC) + self._expiry
            self._jobs.succeed(job_id, worker_id)
        except OperationCancelled:
            with self._lock:
                record = self._records.get(job_id)
                if record:
                    record.stage = "cancelled"
            try:
                self._jobs.finish_cancel(job_id, worker_id)
            except Exception:
                pass
        except Exception as exc:
            LocalDiagnosticLog(self._context.paths.logs).record(
                "answer_job_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
            message = str(exc)[:500]
            with self._lock:
                record = self._records.get(job_id)
                if record:
                    record.stage = "failed"
                    record.error = message
            try:
                self._jobs.fail(
                    job_id,
                    worker_id,
                    error_code="answer_failed",
                    error_message=message,
                    retryable=False,
                )
            except Exception:
                pass
        finally:
            gateway.close()
            with self._lock:
                self._signals.pop(job_id, None)

    def _set_stage(self, job_id: str, stage: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record:
                record.stage = stage

    def _prune(self) -> None:
        now = datetime.now(UTC)
        with self._lock:
            expired = [
                job_id
                for job_id, record in self._records.items()
                if record.expires_at <= now
            ]
            for job_id in expired:
                self._records.pop(job_id, None)
                self._signals.pop(job_id, None)
