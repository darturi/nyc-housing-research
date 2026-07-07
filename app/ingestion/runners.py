from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from sqlalchemy.orm import Session as DbSession

from app.models.ingestion_run import IngestionRun

T = TypeVar("T")


def finish_run(
    db: DbSession,
    run: IngestionRun,
    status: str,
    records_created: int = 0,
    records_updated: int = 0,
    records_skipped: int = 0,
    error_message: str | None = None,
) -> None:
    run.status = status
    run.finished_at = datetime.now(UTC).replace(tzinfo=None)
    run.records_created = records_created
    run.records_updated = records_updated
    run.records_skipped = records_skipped
    run.error_message = error_message
    db.commit()


def record_ingestion_run(
    db: DbSession,
    run_type: str,
    operation: Callable[[], tuple[T, int, int, int]],
    source_id: str | None = None,
    source_version_id: str | None = None,
) -> T:
    run = IngestionRun(
        source_id=source_id,
        source_version_id=source_version_id,
        run_type=run_type,
        status="started",
        started_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(run)
    db.commit()
    try:
        result, created, updated, skipped = operation()
    except Exception as exc:
        finish_run(db, run, "failed", error_message=str(exc))
        raise
    finish_run(db, run, "succeeded", created, updated, skipped)
    return result
