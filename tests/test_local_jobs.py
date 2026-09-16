from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.jobs.mutations import run_corpus_mutation
from app.jobs.runtime import (
    CancellationSignal,
    Deadline,
    OperationCancelled,
    OperationDeadlineExceeded,
)
from app.jobs.service import (
    InvalidJobTransition,
    JobConflict,
    JobService,
    JobState,
)
from app.storage.database import LocalStorage
from app.storage.schema import jobs as jobs_table
from app.storage.schema import maintenance_state
from app.workspace.paths import resolve_workspace_paths


@pytest.fixture
def job_service(tmp_path):
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    try:
        yield JobService(storage.state_engine)
    finally:
        storage.close()


def test_job_lifecycle_checkpoints_and_cancels(job_service) -> None:
    job = job_service.create(
        "corpus_update",
        "generation:one",
        resume={"operation": "update", "source": "one"},
    )
    assert job.resume == {"operation": "update", "source": "one"}
    running = job_service.claim(job.id, "worker-one")
    assert running.state == JobState.RUNNING

    checkpoint = job_service.checkpoint(
        job.id,
        "worker-one",
        stage="download",
        current=3,
        total=10,
        resume={"last_artifact": "three"},
    )
    assert checkpoint.resume == {"last_artifact": "three"}

    requested = job_service.request_cancel(job.id)
    assert requested.state == JobState.CANCEL_REQUESTED
    cancelled = job_service.finish_cancel(job.id, "worker-one")
    assert cancelled.state == JobState.CANCELLED
    with pytest.raises(InvalidJobTransition):
        job_service.resume(job.id)


def test_target_mutation_is_serialized_between_connections(job_service) -> None:
    def create_job():
        try:
            return job_service.create("corpus_update", "source:core").id
        except JobConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create_job(), range(2)))

    assert sum(result is not None for result in results) == 1


def test_interrupted_maintenance_pauses_but_answer_requires_resubmit(
    job_service,
) -> None:
    recovery_time = datetime.now(UTC) + timedelta(minutes=5)
    maintenance = job_service.create("corpus_update", "source:one")
    answer = job_service.create("answer", "answer:one")
    short_mutation = job_service.create("corpus_rollback", "corpus:other")
    job_service.claim(maintenance.id, "dead-worker", lease_seconds=-1)
    job_service.claim(answer.id, "dead-worker", lease_seconds=-1)
    job_service.claim(short_mutation.id, "dead-worker", lease_seconds=-1)

    assert job_service.recover_interrupted(now=recovery_time) == 3
    recovered_maintenance = job_service.get(maintenance.id)
    recovered_answer = job_service.get(answer.id)
    assert recovered_maintenance.state == JobState.PAUSED
    assert recovered_maintenance.retryable is True
    assert recovered_answer.state == JobState.FAILED
    assert recovered_answer.retryable is False
    assert recovered_answer.error_code == "process_interrupted"
    recovered_mutation = job_service.get(short_mutation.id)
    assert recovered_mutation.state == JobState.FAILED
    assert recovered_mutation.retryable is False


def test_short_corpus_mutation_uses_the_shared_writer_target(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    service = JobService(storage.state_engine)
    service.create("corpus_update", "corpus:core")
    called = False

    def mutation():
        nonlocal called
        called = True

    try:
        with pytest.raises(JobConflict, match="already has active job"):
            run_corpus_mutation(storage, "corpus_rollback", mutation)
        assert called is False
    finally:
        storage.close()


def test_error_messages_are_bounded_and_job_metadata_has_no_payload(
    job_service,
) -> None:
    job = job_service.create("corpus_update", "source:two")
    job_service.claim(job.id, "worker")
    failed = job_service.fail(
        job.id,
        "worker",
        error_code="provider/error!",
        error_message="secret-looking details " * 200,
        retryable=True,
    )

    assert failed.error_code == "providererror"
    assert len(failed.error_message or "") == 1000
    assert "question" not in failed.resume


def test_runtime_cancellation_and_deadline() -> None:
    signal = CancellationSignal()
    signal.cancel()
    with pytest.raises(OperationCancelled):
        signal.raise_if_cancelled()

    expired = Deadline.after(0)
    with pytest.raises(OperationDeadlineExceeded):
        expired.raise_if_expired()


def test_maintenance_barrier_blocks_claim_and_resume(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    service = JobService(storage.state_engine)
    queued = service.create("answer", "queued")
    paused = service.create("corpus_update", "paused")
    with storage.state_engine.begin() as connection:
        connection.execute(
            update(maintenance_state)
            .where(maintenance_state.c.id == 1)
            .values(active=True, operation="fixture", started_at=datetime.now(UTC))
        )
        connection.execute(
            update(jobs_table)
            .where(jobs_table.c.id == paused.id)
            .values(state="paused")
        )
    try:
        with pytest.raises(JobConflict, match="maintenance"):
            service.claim(queued.id, "worker")
        with pytest.raises(JobConflict, match="maintenance"):
            service.resume(paused.id)
    finally:
        storage.close()
