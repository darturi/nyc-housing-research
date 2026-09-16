from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, replace

from app.exporting.service import ResearchExporter
from app.hpd.cache import CachedPropertyRepository
from app.hpd.connector import (
    PropertyConnectorError,
    PropertyQuery,
    PropertySearchResponse,
)
from app.jobs.runtime import (
    CancellationSignal,
    Deadline,
    OperationCancelled,
    OperationDeadlineExceeded,
)
from app.jobs.service import JobService


@dataclass(frozen=True)
class CompletePropertyExport:
    job_id: str
    path: str
    page_count: int
    row_count: int
    is_complete: bool
    interruption_reason: str | None
    continuation: str | None


def export_complete_property_result(
    repository: CachedPropertyRepository,
    exporter: ResearchExporter,
    jobs: JobService,
    query: PropertyQuery,
    *,
    max_pages: int = 20,
    deadline: Deadline | None = None,
    cancellation: CancellationSignal | None = None,
    job_id: str | None = None,
    worker_id: str | None = None,
    resume: dict[str, object] | None = None,
) -> CompletePropertyExport:
    if query.continuation:
        raise ValueError("Complete export must start without a continuation token.")
    if not 1 <= max_pages <= 100:
        raise ValueError("Complete export max pages must be between 1 and 100.")
    identity = hashlib.sha256(
        json.dumps(asdict(query), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (job_id is None) != (worker_id is None):
        raise ValueError(
            "Property export job and worker IDs must be supplied together."
        )
    if job_id is None:
        job = jobs.create("property_complete_export", f"property-export:{identity}")
        job_id = job.id
        worker_id = f"property-export-{os.getpid()}"
        jobs.claim(job_id, worker_id, lease_seconds=300)
    assert worker_id is not None
    resume_base = dict(resume or {})
    pages: list[PropertySearchResponse] = []
    pinned: list[PropertyQuery] = []
    next_query = query
    interruption = None
    try:
        for page_number in range(1, max_pages + 1):
            try:
                if cancellation:
                    cancellation.raise_if_cancelled()
                if deadline:
                    deadline.raise_if_expired()
                page = repository.search(next_query, deadline=deadline)
            except (
                OperationCancelled,
                OperationDeadlineExceeded,
                PropertyConnectorError,
            ) as exc:
                if not pages:
                    raise
                interruption = type(exc).__name__
                break
            if page.requires_selection:
                raise ValueError(
                    "Complete export requires an unambiguous building selection."
                )
            pages.append(page)
            repository.pin(next_query, pinned=True)
            pinned.append(next_query)
            jobs.heartbeat(job_id, worker_id, lease_seconds=300)
            jobs.checkpoint(
                job_id,
                worker_id,
                stage="fetching_property_pages",
                current=page_number,
                total=max_pages,
                resume=resume_base
                | {
                    "page_count": page_number,
                    "row_count": sum(len(item.records) for item in pages),
                    "has_continuation": bool(page.continuation),
                },
            )
            if page.is_complete or not page.continuation:
                break
            next_query = replace(query, continuation=page.continuation)
        if pages[-1].continuation and len(pages) == max_pages:
            interruption = "max_pages_reached"
        combined = _combine(query, pages, interruption)
        output = exporter.property_csv(combined)
        # Release the cache barrier before advertising job completion. This keeps
        # backup/restore from observing a succeeded export with pinned pages.
        for pinned_query in reversed(pinned):
            repository.pin(pinned_query, pinned=False)
        pinned.clear()
        jobs.checkpoint(
            job_id,
            worker_id,
            stage="export_created",
            current=len(pages),
            total=max_pages,
            resume=resume_base
            | {
                "page_count": len(pages),
                "row_count": len(combined.records),
                "is_complete": combined.is_complete,
                "interruption_reason": interruption,
                "continuation": combined.continuation,
                "output_filename": output.name,
            },
        )
        if jobs.get(job_id).state.value == "cancel_requested":
            jobs.finish_cancel(job_id, worker_id)
        else:
            jobs.succeed(job_id, worker_id)
        return CompletePropertyExport(
            job_id=job_id,
            path=str(output),
            page_count=len(pages),
            row_count=len(combined.records),
            is_complete=combined.is_complete,
            interruption_reason=interruption,
            continuation=combined.continuation,
        )
    except Exception as exc:
        if jobs.get(job_id).state.value == "cancel_requested":
            jobs.finish_cancel(job_id, worker_id)
        else:
            jobs.fail(
                job_id,
                worker_id,
                error_code="property_complete_export_failed",
                error_message=str(exc),
                retryable=True,
            )
        raise
    finally:
        # Preserve the primary exception while making a best effort to clean up
        # any pins left by a failed request or failed normal-path unpin.
        for pinned_query in reversed(pinned):
            try:
                repository.pin(pinned_query, pinned=False)
            except Exception:
                pass


def _combine(
    query: PropertyQuery,
    pages: list[PropertySearchResponse],
    interruption: str | None,
) -> PropertySearchResponse:
    if not pages:
        raise ValueError("No property pages were available to export.")
    first = pages[0]
    last = pages[-1]
    # The keyset pagination contract prevents duplicates in a stable dataset, but
    # the live publisher can change between page requests. Preserve the first
    # occurrence so a moving row does not appear twice in an export.
    unique_records = {}
    for page in pages:
        for record in page.records:
            unique_records.setdefault(record.violation_id, record)
    records = tuple(unique_records.values())
    return PropertySearchResponse(
        query=query,
        candidates=first.candidates,
        records=records,
        requires_selection=False,
        continuation=last.continuation,
        is_complete=interruption is None and last.is_complete,
        returned_count=len(records),
        fetched_at=last.fetched_at,
        dataset_id=first.dataset_id,
        dataset_url=first.dataset_url,
        connector_version=first.connector_version,
        source_status=first.source_status,
        total_count=None,
        has_more=bool(last.continuation),
        next_cursor=last.continuation,
        source_update_time=first.source_update_time,
        fetch_started_at=next(
            (page.fetch_started_at for page in pages if page.fetch_started_at),
            first.fetched_at,
        ),
        fetch_completed_at=next(
            (
                page.fetch_completed_at
                for page in reversed(pages)
                if page.fetch_completed_at
            ),
            last.fetched_at,
        ),
        cache_status="multi_page_export",
        stale=any(page.stale for page in pages),
    )
