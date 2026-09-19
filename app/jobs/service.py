from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Engine, insert, or_, select, update

from app.storage.schema import jobs, maintenance_state


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


ACTIVE_TARGET_STATES = {
    JobState.QUEUED,
    JobState.RUNNING,
    JobState.PAUSED,
    JobState.CANCEL_REQUESTED,
}

RESUMABLE_JOB_TYPES = {
    "corpus_install",
    "corpus_update",
    "corpus_check",
    "corpus_remove",
    "corpus_restore",
    "corpus_index",
    "property_complete_export",
    "resource_add",
    "resource_replace",
}


class JobConflict(RuntimeError):
    pass


class JobNotFound(LookupError):
    pass


class InvalidJobTransition(RuntimeError):
    pass


@dataclass(frozen=True)
class JobRecord:
    id: str
    job_type: str
    target_id: str
    state: JobState
    stage: str
    progress_current: int
    progress_total: int | None
    retryable: bool
    resume: dict[str, Any]
    error_code: str | None
    error_message: str | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    cancel_requested_at: datetime | None
    created_at: datetime
    updated_at: datetime


class JobService:
    def __init__(self, state_engine: Engine) -> None:
        self._engine = state_engine

    def create(
        self,
        job_type: str,
        target_id: str,
        *,
        stage: str = "queued",
        resume: dict[str, Any] | None = None,
    ) -> JobRecord:
        now = _utc_now()
        job_id = str(uuid.uuid4())
        with self._immediate_transaction() as connection:
            self._assert_no_maintenance(connection)
            conflict = connection.scalar(
                select(jobs.c.id).where(
                    jobs.c.target_id == target_id,
                    jobs.c.state.in_(state.value for state in ACTIVE_TARGET_STATES),
                )
            )
            if conflict is not None:
                raise JobConflict(
                    f"Target {target_id!r} already has active job {conflict}."
                )
            connection.execute(
                insert(jobs).values(
                    id=job_id,
                    job_type=job_type,
                    target_id=target_id,
                    state=JobState.QUEUED.value,
                    stage=stage,
                    progress_current=0,
                    progress_total=None,
                    retryable=True,
                    resume_json=json.dumps(resume or {}, sort_keys=True),
                    created_at=now,
                    updated_at=now,
                )
            )
        return self.get(job_id)

    def get(self, job_id: str) -> JobRecord:
        with self._engine.connect() as connection:
            row = (
                connection.execute(select(jobs).where(jobs.c.id == job_id))
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise JobNotFound(job_id)
        return _record(row)

    def list(self, *, limit: int = 100) -> list[JobRecord]:
        with self._engine.connect() as connection:
            rows = connection.execute(
                select(jobs).order_by(jobs.c.created_at.desc()).limit(limit)
            ).mappings()
            return [_record(row) for row in rows]

    def claim(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 30,
    ) -> JobRecord:
        now = _utc_now()
        with self._immediate_transaction() as connection:
            self._assert_no_maintenance(connection)
            row = self._locked_row(connection, job_id)
            state = JobState(row["state"])
            if state != JobState.QUEUED:
                raise InvalidJobTransition(f"Cannot claim a {state.value} job.")
            competing = connection.scalar(
                select(jobs.c.id).where(
                    jobs.c.target_id == row["target_id"],
                    jobs.c.id != job_id,
                    or_(
                        jobs.c.state == JobState.RUNNING.value,
                        jobs.c.state == JobState.CANCEL_REQUESTED.value,
                    ),
                )
            )
            if competing is not None:
                raise JobConflict(
                    f"Target {row['target_id']!r} is being changed by {competing}."
                )
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state=JobState.RUNNING.value,
                    lease_owner=worker_id,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
        return self.get(job_id)

    def heartbeat(
        self, job_id: str, worker_id: str, *, lease_seconds: int = 30
    ) -> JobRecord:
        now = _utc_now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.lease_owner == worker_id,
                    jobs.c.state.in_(
                        [JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]
                    ),
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise InvalidJobTransition("Worker does not own a running job lease.")
        return self.get(job_id)

    def checkpoint(
        self,
        job_id: str,
        worker_id: str,
        *,
        stage: str,
        current: int,
        total: int | None,
        resume: dict[str, Any],
    ) -> JobRecord:
        if current < 0 or (total is not None and (total < 0 or current > total)):
            raise ValueError("Invalid job progress.")
        now = _utc_now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.lease_owner == worker_id,
                    jobs.c.state.in_(
                        [JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]
                    ),
                )
                .values(
                    stage=stage,
                    progress_current=current,
                    progress_total=total,
                    resume_json=json.dumps(resume, sort_keys=True),
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise InvalidJobTransition("Worker does not own a running job lease.")
        return self.get(job_id)

    def request_cancel(self, job_id: str) -> JobRecord:
        now = _utc_now()
        with self._immediate_transaction() as connection:
            self._assert_no_maintenance(connection)
            row = self._locked_row(connection, job_id)
            state = JobState(row["state"])
            if state in {JobState.QUEUED, JobState.PAUSED}:
                values = {
                    "state": JobState.CANCELLED.value,
                    "stage": "cancelled",
                    "retryable": False,
                    "cancel_requested_at": now,
                    "updated_at": now,
                }
            elif state == JobState.RUNNING:
                values = {
                    "state": JobState.CANCEL_REQUESTED.value,
                    "cancel_requested_at": now,
                    "updated_at": now,
                }
            elif state == JobState.CANCEL_REQUESTED:
                values = {"updated_at": now}
            else:
                raise InvalidJobTransition(f"Cannot cancel a {state.value} job.")
            connection.execute(update(jobs).where(jobs.c.id == job_id).values(**values))
        return self.get(job_id)

    def finish_cancel(self, job_id: str, worker_id: str) -> JobRecord:
        return self._finish_owned(
            job_id,
            worker_id,
            required_state=JobState.CANCEL_REQUESTED,
            new_state=JobState.CANCELLED,
            stage="cancelled",
            retryable=False,
        )

    def succeed(self, job_id: str, worker_id: str) -> JobRecord:
        return self._finish_owned(
            job_id,
            worker_id,
            required_state=JobState.RUNNING,
            new_state=JobState.SUCCEEDED,
            stage="complete",
            retryable=False,
        )

    def fail(
        self,
        job_id: str,
        worker_id: str,
        *,
        error_code: str,
        error_message: str,
        retryable: bool,
    ) -> JobRecord:
        now = _utc_now()
        with self._engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.lease_owner == worker_id,
                    jobs.c.state.in_(
                        [JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]
                    ),
                )
                .values(
                    state=JobState.FAILED.value,
                    stage="failed",
                    retryable=retryable,
                    error_code=_sanitize_code(error_code),
                    error_message=_sanitize_message(error_message),
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
        if result.rowcount != 1:
            raise InvalidJobTransition("Worker does not own a running job lease.")
        return self.get(job_id)

    def resume(self, job_id: str) -> JobRecord:
        now = _utc_now()
        with self._immediate_transaction() as connection:
            self._assert_no_maintenance(connection)
            row = self._locked_row(connection, job_id)
            state = JobState(row["state"])
            if state not in {JobState.PAUSED, JobState.FAILED} or not row["retryable"]:
                raise InvalidJobTransition(f"Cannot resume a {state.value} job.")
            conflict = connection.scalar(
                select(jobs.c.id).where(
                    jobs.c.target_id == row["target_id"],
                    jobs.c.id != job_id,
                    jobs.c.state.in_(state.value for state in ACTIVE_TARGET_STATES),
                )
            )
            if conflict is not None:
                raise JobConflict(
                    f"Target {row['target_id']!r} already has active job {conflict}."
                )
            connection.execute(
                update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    state=JobState.QUEUED.value,
                    stage="queued",
                    error_code=None,
                    error_message=None,
                    updated_at=now,
                )
            )
        return self.get(job_id)

    def recover_interrupted(self, *, now: datetime | None = None) -> int:
        now = _utc_now() if now is None else now
        recovered = 0
        with self._immediate_transaction() as connection:
            rows = connection.execute(
                select(jobs).where(
                    jobs.c.state.in_(
                        [JobState.RUNNING.value, JobState.CANCEL_REQUESTED.value]
                    ),
                    or_(
                        jobs.c.lease_expires_at.is_(None),
                        jobs.c.lease_expires_at <= now,
                    ),
                )
            ).mappings()
            for row in rows:
                resumable = row["job_type"] in RESUMABLE_JOB_TYPES
                connection.execute(
                    update(jobs)
                    .where(jobs.c.id == row["id"])
                    .values(
                        state=(
                            JobState.PAUSED.value
                            if resumable
                            else JobState.FAILED.value
                        ),
                        stage="interrupted",
                        retryable=resumable,
                        error_code="process_interrupted",
                        error_message=(
                            "Worker stopped; resume from the last checkpoint."
                            if resumable
                            else "Operation stopped; explicitly submit it again."
                        ),
                        lease_owner=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                )
                recovered += 1
        return recovered

    def _finish_owned(
        self,
        job_id: str,
        worker_id: str,
        *,
        required_state: JobState,
        new_state: JobState,
        stage: str,
        retryable: bool,
    ) -> JobRecord:
        with self._engine.begin() as connection:
            result = connection.execute(
                update(jobs)
                .where(
                    jobs.c.id == job_id,
                    jobs.c.lease_owner == worker_id,
                    jobs.c.state == required_state.value,
                )
                .values(
                    state=new_state.value,
                    stage=stage,
                    retryable=retryable,
                    lease_owner=None,
                    lease_expires_at=None,
                    updated_at=_utc_now(),
                )
            )
        if result.rowcount != 1:
            raise InvalidJobTransition("Worker does not own the required job state.")
        return self.get(job_id)

    def _locked_row(self, connection, job_id: str):
        row = (
            connection.execute(select(jobs).where(jobs.c.id == job_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise JobNotFound(job_id)
        return row

    @staticmethod
    def _assert_no_maintenance(connection) -> None:
        if connection.scalar(
            select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
        ):
            raise JobConflict(
                "Workspace maintenance is active; no job was admitted or resumed."
            )

    def _immediate_transaction(self):
        return _ImmediateTransaction(self._engine)


class _ImmediateTransaction:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection = None

    def __enter__(self):
        self._connection = self._engine.connect()
        self._connection.exec_driver_sql("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        assert self._connection is not None
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()


def _record(row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        job_type=row["job_type"],
        target_id=row["target_id"],
        state=JobState(row["state"]),
        stage=row["stage"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        retryable=row["retryable"],
        resume=json.loads(row["resume_json"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        cancel_requested_at=row["cancel_requested_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _sanitize_code(value: str) -> str:
    allowed = (
        character for character in value if character.isalnum() or character in "_-"
    )
    return "".join(allowed)[:80] or "job_error"


def _sanitize_message(value: str) -> str:
    return " ".join(value.replace("\x00", "").split())[:1000]


def _utc_now() -> datetime:
    return datetime.now(UTC)
