from __future__ import annotations

import uuid
from collections.abc import Callable

from app.jobs.service import JobRecord, JobService
from app.storage.database import LocalStorage


def run_corpus_mutation[T](
    storage: LocalStorage,
    job_type: str,
    operation: Callable[[], T],
) -> tuple[JobRecord, T]:
    """Serialize a short non-resumable corpus mutation with background jobs."""
    jobs = JobService(storage.state_engine)
    job = jobs.create(job_type, "corpus:core", resume={"operation": job_type})
    worker_id = f"corpus-mutation-{uuid.uuid4()}"
    jobs.claim(job.id, worker_id, lease_seconds=300)
    try:
        result = operation()
    except Exception as exc:
        jobs.fail(
            job.id,
            worker_id,
            error_code="corpus_mutation_failed",
            error_message=str(exc),
            retryable=False,
        )
        raise
    jobs.succeed(job.id, worker_id)
    return jobs.get(job.id), result
