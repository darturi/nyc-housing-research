from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from app.corpus.resources import ResourceMetadata, ResourceService
from app.corpus.service import CorpusValidationError
from app.jobs.runtime import OperationCancelled
from app.jobs.service import InvalidJobTransition, JobRecord, JobService, JobState
from app.maintenance.logging import LocalDiagnosticLog
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


def run_resource_mutation[T](
    storage: LocalStorage,
    job_type: str,
    operation: Callable[[str], T],
) -> tuple[JobRecord, T]:
    jobs = JobService(storage.state_engine)
    job = jobs.create(job_type, "corpus:core", resume={"operation": job_type})
    worker_id = f"resource-mutation-{uuid.uuid4()}"
    jobs.claim(job.id, worker_id, lease_seconds=300)
    try:
        result = operation(job.id)
    except Exception as exc:
        jobs.fail(
            job.id,
            worker_id,
            error_code="resource_mutation_failed",
            error_message=str(exc),
            retryable=False,
        )
        raise
    jobs.succeed(job.id, worker_id)
    return jobs.get(job.id), result


class ResourceJobs:
    def __init__(self, context: WorkspaceContext, storage: LocalStorage) -> None:
        self._base_context = context
        self._storage = storage
        self._jobs = JobService(storage.state_engine)
        self._jobs.recover_interrupted()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="resource-import"
        )
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._staging = context.paths.artifacts / "resource-staging"
        self._staging.mkdir(parents=True, exist_ok=True)

    @property
    def _context(self) -> WorkspaceContext:
        return self._base_context.current()

    def submit_add(
        self,
        content: bytes,
        *,
        filename: str,
        content_type: str | None,
        metadata: ResourceMetadata,
    ) -> JobRecord:
        stage_id = str(uuid.uuid4())
        self._write_stage(
            stage_id,
            content,
            {
                "operation": "add",
                "filename": filename,
                "content_type": content_type,
                "metadata": asdict(metadata),
            },
        )
        try:
            job = self._jobs.create(
                "resource_add",
                "corpus:core",
                resume={"operation": "add", "stage_id": stage_id},
            )
        except Exception:
            self._cleanup_stage(stage_id)
            raise
        self._schedule(job.id)
        return job

    def submit_replace(
        self,
        resource_id: str,
        content: bytes,
        *,
        filename: str,
        content_type: str | None,
        metadata: ResourceMetadata | None,
        expected_version_id: str | None,
    ) -> JobRecord:
        stage_id = str(uuid.uuid4())
        self._write_stage(
            stage_id,
            content,
            {
                "operation": "replace",
                "resource_id": resource_id,
                "filename": filename,
                "content_type": content_type,
                "metadata": asdict(metadata) if metadata else None,
                "expected_version_id": expected_version_id,
            },
        )
        try:
            job = self._jobs.create(
                "resource_replace",
                "corpus:core",
                resume={"operation": "replace", "stage_id": stage_id},
            )
        except Exception:
            self._cleanup_stage(stage_id)
            raise
        self._schedule(job.id)
        return job

    def get(self, job_id: str) -> JobRecord:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> JobRecord:
        record = self._jobs.request_cancel(job_id)
        if record.state == JobState.CANCELLED:
            stage_id = record.resume.get("stage_id")
            if isinstance(stage_id, str):
                self._cleanup_stage(stage_id)
        return record

    def resume(self, job_id: str) -> JobRecord:
        existing = self._jobs.get(job_id)
        if existing.job_type not in {"resource_add", "resource_replace"}:
            raise InvalidJobTransition("This is not a resource import job.")
        resumed = self._jobs.resume(job_id)
        self._schedule(job_id)
        return resumed

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
        worker_id = f"resource-web-{uuid.uuid4()}"
        storage = LocalStorage.open(self._context.paths)
        jobs = JobService(storage.state_engine)
        try:
            record = jobs.claim(job_id, worker_id, lease_seconds=300)
            stage_id = record.resume.get("stage_id")
            if not isinstance(stage_id, str):
                raise ValueError("Resource staging state is missing.")
            metadata_payload, content = self._read_stage(stage_id)
            if jobs.get(job_id).state == JobState.CANCEL_REQUESTED:
                raise OperationCancelled("Resource import was cancelled.")
            jobs.checkpoint(
                job_id,
                worker_id,
                stage="extracting",
                current=0,
                total=1,
                resume=dict(record.resume),
            )
            operation = metadata_payload.get("operation")
            service = ResourceService(storage)
            raw_metadata = metadata_payload.get("metadata")
            resource_metadata = (
                ResourceMetadata(**raw_metadata)
                if isinstance(raw_metadata, dict)
                else None
            )
            if operation == "add" and resource_metadata is not None:
                result = service.add(
                    content,
                    filename=str(metadata_payload["filename"]),
                    content_type=_optional_string(metadata_payload.get("content_type")),
                    metadata=resource_metadata,
                    operation_id=job_id,
                )
            elif operation == "replace":
                result = service.replace_file(
                    str(metadata_payload["resource_id"]),
                    content,
                    filename=str(metadata_payload["filename"]),
                    content_type=_optional_string(metadata_payload.get("content_type")),
                    metadata=resource_metadata,
                    expected_version_id=_optional_string(
                        metadata_payload.get("expected_version_id")
                    ),
                    operation_id=job_id,
                )
            else:
                raise ValueError("Resource job operation is invalid.")
            jobs.checkpoint(
                job_id,
                worker_id,
                stage="activated",
                current=1,
                total=1,
                resume=dict(record.resume) | {"result": result.as_dict()},
            )
            jobs.succeed(job_id, worker_id)
            self._cleanup_stage(stage_id)
        except OperationCancelled:
            try:
                current = jobs.get(job_id)
                stage_id = current.resume.get("stage_id")
                jobs.finish_cancel(job_id, worker_id)
                if isinstance(stage_id, str):
                    self._cleanup_stage(stage_id)
            except Exception:
                pass
        except Exception as exc:
            LocalDiagnosticLog(self._context.paths.logs).record(
                "resource_job_failed",
                job_id=job_id,
                error_type=type(exc).__name__,
            )
            try:
                current = jobs.get(job_id)
                if current.state == JobState.CANCEL_REQUESTED:
                    jobs.finish_cancel(job_id, worker_id)
                else:
                    jobs.fail(
                        job_id,
                        worker_id,
                        error_code=(
                            "resource_validation_failed"
                            if isinstance(exc, CorpusValidationError)
                            else "resource_import_failed"
                        ),
                        error_message=str(exc),
                        retryable=not isinstance(exc, CorpusValidationError),
                    )
            except Exception:
                pass
        finally:
            try:
                current = jobs.get(job_id)
                if current.state in {JobState.CANCELLED, JobState.SUCCEEDED} or (
                    current.state == JobState.FAILED and not current.retryable
                ):
                    stage_id = current.resume.get("stage_id")
                    if isinstance(stage_id, str):
                        self._cleanup_stage(stage_id)
            finally:
                storage.close()

    def _write_stage(
        self, stage_id: str, content: bytes, metadata: dict[str, object]
    ) -> None:
        _write_atomic(self._stage_path(stage_id, ".bin"), content)
        try:
            _write_atomic(
                self._stage_path(stage_id, ".json"),
                json.dumps(metadata, sort_keys=True).encode("utf-8"),
            )
        except Exception:
            self._stage_path(stage_id, ".bin").unlink(missing_ok=True)
            raise

    def _read_stage(self, stage_id: str) -> tuple[dict[str, object], bytes]:
        try:
            metadata = json.loads(
                self._stage_path(stage_id, ".json").read_text(encoding="utf-8")
            )
            content = self._stage_path(stage_id, ".bin").read_bytes()
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Resource staging files are unavailable.") from exc
        if not isinstance(metadata, dict):
            raise ValueError("Resource staging metadata is invalid.")
        return metadata, content

    def _cleanup_stage(self, stage_id: str) -> None:
        self._stage_path(stage_id, ".bin").unlink(missing_ok=True)
        self._stage_path(stage_id, ".json").unlink(missing_ok=True)

    def _stage_path(self, stage_id: str, suffix: str) -> Path:
        try:
            normalized = str(uuid.UUID(stage_id))
        except ValueError as exc:
            raise ValueError("Resource staging identifier is invalid.") from exc
        path = (self._staging / f"{normalized}{suffix}").resolve()
        try:
            path.relative_to(self._staging.resolve())
        except ValueError as exc:
            raise ValueError("Resource staging path is invalid.") from exc
        return path


def _write_atomic(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _optional_string(value: object) -> str | None:
    return str(value) if value else None
