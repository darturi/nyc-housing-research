from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from decimal import Decimal

from app.corpus.download import download_source_artifacts
from app.corpus.indexing import CorpusEmbeddingIndexer, IndexingEstimate
from app.corpus.manifests import load_all_manifests
from app.corpus.service import CorpusService, CorpusValidationError, SourceArtifact
from app.credentials.store import CredentialResolver
from app.jobs.runtime import OperationCancelled
from app.jobs.service import (
    InvalidJobTransition,
    JobRecord,
    JobService,
    JobState,
)
from app.maintenance.logging import LocalDiagnosticLog
from app.providers.gateway import ProviderGateway
from app.providers.profiles import ProfileKind, get_configured_profile
from app.storage.database import LocalStorage
from app.usage.ledger import SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext

Downloader = Callable[..., list[SourceArtifact]]
PROGRESS_PATTERN = re.compile(r"Downloading source (?P<current>\d+)/(?P<total>\d+):")


class CorpusMaintenanceJobs:
    """Run durable, keyless corpus acquisition jobs outside request threads."""

    def __init__(
        self,
        context: WorkspaceContext,
        storage: LocalStorage,
        *,
        downloader: Downloader | None = None,
    ) -> None:
        self._context = context
        self._storage = storage
        self._jobs = JobService(storage.state_engine)
        self._jobs.recover_interrupted()
        self._downloader = downloader or download_source_artifacts
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="corpus-maintenance",
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()

    def estimate_index(self) -> tuple[IndexingEstimate, bool, str | None]:
        profile = get_configured_profile(self._context.settings, ProfileKind.EMBEDDING)
        if not profile.compatibility_verified:
            raise ValueError(
                "The custom embedding endpoint must pass `profiles check embedding` "
                "before indexing."
            )
        gateway = ProviderGateway(self._context, UsageLedger(self._storage))
        try:
            estimate = CorpusEmbeddingIndexer(self._storage, gateway).estimate_active(
                profile
            )
        finally:
            gateway.close()
        credential_source = None
        if profile.paid:
            _credential, credential_source = CredentialResolver(self._context).resolve(
                profile.credential_slot or profile.provider
            )
        return estimate, profile.paid, credential_source

    def submit(
        self,
        operation: str,
        *,
        source: str | None = None,
        sources: list[str] | None = None,
        pack_id: str | None = None,
        allow_partial: bool = False,
        approve_cost: bool = False,
        max_cost_usd: Decimal | None = None,
        batch_size: int = 32,
    ) -> JobRecord:
        resume: dict[str, object] = {
            "operation": operation,
            "source": source,
            "sources": sources,
            "pack_id": pack_id,
            "allow_partial": allow_partial,
        }
        if operation == "index":
            if source is not None or sources is not None or pack_id is not None:
                raise ValueError("Corpus indexing does not accept a source filter.")
            if not 1 <= batch_size <= 128:
                raise ValueError("Embedding batch size must be between 1 and 128.")
            if max_cost_usd is not None and (
                not max_cost_usd.is_finite() or max_cost_usd < 0
            ):
                raise ValueError(
                    "The indexing cost ceiling must be finite and nonnegative."
                )
            estimate, paid, credential_source = self.estimate_index()
            if max_cost_usd is not None and estimate.estimated_cost_usd > max_cost_usd:
                raise ValueError(
                    "Estimated indexing cost exceeds the approved cost ceiling."
                )
            if paid and not approve_cost:
                raise ValueError(
                    "Paid corpus indexing requires explicit cost approval."
                )
            if paid and max_cost_usd is None:
                raise ValueError(
                    "Paid corpus indexing requires an explicit cost ceiling."
                )
            if paid and credential_source is None:
                raise ValueError(
                    "The selected embedding provider credential is missing."
                )
            resume |= {
                "profile_id": estimate.profile_id,
                "approve_cost": approve_cost,
                "max_cost_usd": (
                    str(max_cost_usd) if max_cost_usd is not None else None
                ),
                "batch_size": batch_size,
                "estimated_cost_usd": str(estimate.estimated_cost_usd),
                "estimated_input_tokens": estimate.estimated_input_tokens,
                "chunks_requiring_embedding": estimate.chunks_requiring_embedding,
            }
        else:
            selected = _operation_slugs(operation, source, sources)
            manifests = load_all_manifests()
            if selected and all(
                manifests[slug].role == "optional" for slug in selected
            ):
                resume["allow_partial"] = True
        job = self._jobs.create(
            f"corpus_{operation}",
            "corpus:core",
            resume=resume,
        )
        self._schedule(job.id)
        return job

    def resume(self, job_id: str) -> JobRecord:
        existing = self._jobs.get(job_id)
        _job_operation(existing)
        with self._lock:
            previous = self._futures.get(job_id)
        if previous is not None:
            previous.result()
        resumed = self._jobs.resume(job_id)
        self._schedule(job_id)
        return resumed

    def get(self, job_id: str) -> JobRecord:
        return self._jobs.get(job_id)

    def list(self, *, limit: int = 100) -> list[JobRecord]:
        return self._jobs.list(limit=limit)

    def cancel(self, job_id: str) -> JobRecord:
        return self._jobs.request_cancel(job_id)

    def wait(self, job_id: str) -> JobRecord:
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result()
        return self._jobs.get(job_id)

    def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _schedule(self, job_id: str) -> None:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                raise InvalidJobTransition("The job already has an active worker.")
            self._futures[job_id] = self._executor.submit(self._run, job_id)

    def _run(self, job_id: str) -> None:
        worker_id = f"corpus-web-{uuid.uuid4()}"
        storage = LocalStorage.open(self._context.paths)
        jobs = JobService(storage.state_engine)
        try:
            record = jobs.claim(job_id, worker_id, lease_seconds=300)
            operation = _job_operation(record)
            if operation == "index":
                self._run_index(storage, jobs, record, worker_id)
                return
            source = record.resume.get("source")
            if source is not None and not isinstance(source, str):
                raise ValueError("Corpus job source state is invalid.")
            raw_sources = record.resume.get("sources")
            if raw_sources is not None and (
                not isinstance(raw_sources, list)
                or not all(isinstance(item, str) for item in raw_sources)
            ):
                raise ValueError("Corpus job source selection is invalid.")
            if source is None and raw_sources is None:
                source = _job_source(record)
            slugs = _operation_slugs(operation, source, raw_sources)
            base_resume = {
                "operation": operation,
                "source": source,
                "sources": raw_sources,
                "pack_id": record.resume.get("pack_id"),
                "allow_partial": record.resume.get("allow_partial") is True,
            }

            if operation in {"remove", "restore"}:
                if slugs is None:
                    raise ValueError(
                        "Pack lifecycle operation requires source modules."
                    )
                corpus = CorpusService(storage)
                generation_id = (
                    corpus.remove_sources(slugs)
                    if operation == "remove"
                    else corpus.restore_sources(slugs)
                )
                jobs.checkpoint(
                    job_id,
                    worker_id,
                    stage="activated",
                    current=len(slugs),
                    total=len(slugs),
                    resume=base_resume | {"generation_id": generation_id},
                )
                jobs.succeed(job_id, worker_id)
                return

            def progress(message: str) -> None:
                if jobs.get(job_id).state == JobState.CANCEL_REQUESTED:
                    raise OperationCancelled("Corpus operation was cancelled.")
                match = PROGRESS_PATTERN.match(message)
                current = int(match.group("current")) - 1 if match else 0
                total = int(match.group("total")) if match else None
                jobs.heartbeat(job_id, worker_id, lease_seconds=300)
                jobs.checkpoint(
                    job_id,
                    worker_id,
                    stage="downloading_sources",
                    current=current,
                    total=total,
                    resume=base_resume | {"requested_sources": slugs or "core"},
                )

            artifacts = self._downloader(
                self._context,
                slugs,
                progress=progress,
            )
            if jobs.get(job_id).state == JobState.CANCEL_REQUESTED:
                raise OperationCancelled("Corpus operation was cancelled.")
            if operation == "check":
                check_result = CorpusService(storage).check_artifacts(artifacts)
                jobs.checkpoint(
                    job_id,
                    worker_id,
                    stage="checked",
                    current=len(artifacts),
                    total=len(artifacts),
                    resume=base_resume | {"check_result": check_result},
                )
                jobs.succeed(job_id, worker_id)
                return
            jobs.checkpoint(
                job_id,
                worker_id,
                stage="parse_and_activate",
                current=len(artifacts),
                total=len(artifacts),
                resume=base_resume
                | {"downloaded_sources": [item.slug for item in artifacts]},
            )
            generation_id = CorpusService(storage).install_artifacts(
                artifacts,
                activate=True,
                allow_partial=base_resume["allow_partial"],
            )
            if jobs.get(job_id).state == JobState.CANCEL_REQUESTED:
                jobs.finish_cancel(job_id, worker_id)
                return
            jobs.checkpoint(
                job_id,
                worker_id,
                stage="activated",
                current=len(artifacts),
                total=len(artifacts),
                resume=base_resume | {"generation_id": generation_id},
            )
            jobs.succeed(job_id, worker_id)
        except OperationCancelled:
            try:
                jobs.finish_cancel(job_id, worker_id)
            except Exception:
                pass
        except Exception as exc:
            LocalDiagnosticLog(self._context.paths.logs).record(
                "corpus_job_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
            try:
                if jobs.get(job_id).state == JobState.CANCEL_REQUESTED:
                    jobs.finish_cancel(job_id, worker_id)
                else:
                    error_code = "corpus_operation_failed"
                    if isinstance(exc, SpendDenied):
                        error_code = "budget_denied"
                    elif isinstance(exc, CorpusValidationError):
                        error_code = "corpus_validation_failed"
                    jobs.fail(
                        job_id,
                        worker_id,
                        error_code=error_code,
                        error_message=str(exc),
                        retryable=True,
                    )
            except Exception:
                pass
        finally:
            storage.close()

    def _run_index(
        self,
        storage: LocalStorage,
        jobs: JobService,
        record: JobRecord,
        worker_id: str,
    ) -> None:
        profile_id = record.resume.get("profile_id")
        if not isinstance(profile_id, str):
            raise ValueError("Corpus indexing profile state is missing.")
        profile = get_configured_profile(self._context.settings, ProfileKind.EMBEDDING)
        if profile.id != profile_id:
            raise ValueError(
                "The embedding profile configuration changed after this job was "
                "created; start a new indexing job."
            )
        approve_cost = record.resume.get("approve_cost") is True
        batch_size = int(record.resume.get("batch_size", 32))
        raw_ceiling = record.resume.get("max_cost_usd")
        ceiling = Decimal(str(raw_ceiling)) if raw_ceiling is not None else None
        ledger = UsageLedger(storage)
        gateway = ProviderGateway(self._context, ledger)
        try:
            indexer = CorpusEmbeddingIndexer(storage, gateway)
            estimate = indexer.estimate_active(profile)
            if ceiling is not None and estimate.estimated_cost_usd > ceiling:
                raise ValueError(
                    "Current indexing estimate exceeds the approved cost ceiling."
                )
            if profile.paid and (not approve_cost or ceiling is None):
                raise ValueError(
                    "Paid corpus indexing approval or cost ceiling is missing."
                )
            credential, _source = CredentialResolver(self._context).resolve(
                profile.credential_slot or profile.provider
            )
            base_resume = dict(record.resume) | {
                "estimated_cost_usd": str(estimate.estimated_cost_usd),
                "estimated_input_tokens": estimate.estimated_input_tokens,
                "chunks_requiring_embedding": estimate.chunks_requiring_embedding,
            }
            jobs.checkpoint(
                record.id,
                worker_id,
                stage="embedding_chunks",
                current=0,
                total=estimate.chunks_requiring_embedding,
                resume=base_resume,
            )

            cancellation = _JobCancellation(jobs, record.id)

            def progress(current: int, total: int) -> None:
                cancellation.raise_if_cancelled()
                jobs.heartbeat(record.id, worker_id, lease_seconds=300)
                jobs.checkpoint(
                    record.id,
                    worker_id,
                    stage="embedding_chunks",
                    current=current,
                    total=total,
                    resume=base_resume | {"embedded_chunks": current},
                )

            result = indexer.index_active(
                profile,
                credential=credential,
                approve_cost=approve_cost,
                operation_id=record.id,
                batch_size=batch_size,
                cancellation=cancellation,
                progress=progress,
            )
            jobs.checkpoint(
                record.id,
                worker_id,
                stage="activated",
                current=result.embedded_chunks,
                total=estimate.chunks_requiring_embedding,
                resume=base_resume
                | {
                    "generation_id": result.generation_id,
                    "embedded_chunks": result.embedded_chunks,
                    "reused_chunks": result.reused_chunks,
                },
            )
            jobs.succeed(record.id, worker_id)
        finally:
            gateway.close()


class _JobCancellation:
    def __init__(self, jobs: JobService, job_id: str) -> None:
        self._jobs = jobs
        self._job_id = job_id

    def raise_if_cancelled(self) -> None:
        if self._jobs.get(self._job_id).state == JobState.CANCEL_REQUESTED:
            raise OperationCancelled("Corpus indexing was cancelled.")


def job_payload(record: JobRecord) -> dict[str, object]:
    payload = asdict(record)
    payload["state"] = record.state.value
    for field in (
        "lease_expires_at",
        "cancel_requested_at",
        "created_at",
        "updated_at",
    ):
        value = payload[field]
        payload[field] = value.isoformat() if value else None
    payload.pop("lease_owner", None)
    return payload


def _operation_slugs(
    operation: str,
    source: str | None,
    sources: list[str] | None = None,
) -> list[str] | None:
    if operation not in {"install", "update", "check", "remove", "restore"}:
        raise ValueError(
            "Corpus operation must be install, update, check, remove, or restore."
        )
    if source is not None and sources is not None:
        raise ValueError("Use either source or sources, not both.")
    manifests = load_all_manifests()
    if source is not None and source not in manifests:
        raise ValueError(f"Unknown source: {source}")
    if sources is not None:
        sources = list(dict.fromkeys(sources))
        if not sources:
            raise ValueError("Select at least one source.")
        unknown = sorted(set(sources) - set(manifests))
        if unknown:
            raise ValueError("Unknown source(s): " + ", ".join(unknown))
    if operation == "install" and source is not None and sources is None:
        raise ValueError("Core install does not accept a single source.")
    if operation in {"remove", "restore"} and sources is None:
        raise ValueError("Pack lifecycle operation requires selected modules.")
    return sources if sources is not None else ([source] if source else None)


def _job_operation(record: JobRecord) -> str:
    prefix = "corpus_"
    if not record.job_type.startswith(prefix):
        raise InvalidJobTransition(
            "Only corpus install/update/index jobs can be resumed from this runner."
        )
    operation = record.job_type.removeprefix(prefix)
    if operation not in {"install", "update", "check", "remove", "restore", "index"}:
        raise InvalidJobTransition("Corpus job operation is invalid.")
    return operation


def _job_source(record: JobRecord) -> str | None:
    prefix = "corpus:"
    if not record.target_id.startswith(prefix):
        raise InvalidJobTransition("Corpus job target is invalid.")
    value = record.target_id.removeprefix(prefix)
    return None if value == "core" else value
