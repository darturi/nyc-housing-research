"""One maintenance owner, protected by an OS lock across database snapshots."""

from __future__ import annotations

from sqlalchemy import select, update

from app.storage.schema import jobs, maintenance_state, paid_call_leases
from app.workspace.locks import FileLease, LockBusy, workspace_root


def maintenance_active(connection) -> bool:
    if not connection.scalar(
        select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
    ):
        return False
    lease = FileLease(workspace_root(connection.engine) / ".maintenance.lock")
    try:
        lease.acquire()
    except LockBusy:
        return True
    try:
        # No live process holds the lock: this is a crashed or restored barrier.
        connection.execute(
            update(maintenance_state)
            .where(maintenance_state.c.id == 1)
            .values(active=False, operation=None, started_at=None)
        )
    finally:
        lease.release()
    return False


class MaintenanceBarrier:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.lease = FileLease(workspace_root(engine) / ".maintenance.lock")

    def enter(self, operation: str, now) -> None:
        self.lease.acquire()
        try:
            from app.jobs.service import JobService
            from app.usage.ledger import recover_usage_attempts

            JobService(self.engine).recover_interrupted(now=now)
            with self.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                if connection.scalar(
                    select(jobs.c.id).where(
                        jobs.c.state.in_(["queued", "running", "cancel_requested"])
                    )
                ):
                    raise RuntimeError(
                        "Pause or finish active jobs before maintenance."
                    )
                recover_usage_attempts(connection, now)
                if connection.scalar(select(paid_call_leases.c.attempt_id)):
                    raise RuntimeError(
                        "Finish active provider calls before maintenance."
                    )
                connection.execute(
                    update(maintenance_state)
                    .where(maintenance_state.c.id == 1)
                    .values(active=True, operation=operation, started_at=now)
                )
                connection.commit()
        except BaseException:
            self.lease.release()
            raise

    def leave(self) -> None:
        if not self.lease.held:
            raise RuntimeError("Only the maintenance owner can release the barrier.")
        try:
            with self.engine.begin() as connection:
                connection.execute(
                    update(maintenance_state)
                    .where(maintenance_state.c.id == 1)
                    .values(active=False, operation=None, started_at=None)
                )
        finally:
            self.lease.release()
