import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, insert, select, update

from app.jobs.service import JobService
from app.maintenance.retention import RetentionError, WorkspaceRetentionService
from app.storage.database import LocalStorage
from app.storage.schema import jobs, maintenance_state, property_cache, usage_events
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext

NOW = datetime(2026, 9, 14, 16, tzinfo=UTC)
OLD = datetime(2025, 1, 5, 16, tzinfo=UTC)


def _workspace(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    context = replace(
        context,
        settings=replace(
            context.settings,
            operational_retention_days=30,
            property_cache_retention_days=30,
            usage_retention_months=12,
        ),
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    return context, storage


def _settled(ledger, attempt_id: str, when: datetime) -> None:
    reservation = ledger.reserve(
        operation_id=f"operation-{attempt_id}",
        attempt_id=attempt_id,
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal("0.10"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
        now=when,
    )
    ledger.settle(
        reservation,
        actual_usd=Decimal("0.05"),
        input_tokens=1,
        output_tokens=1,
        price_snapshot={"fixture": True},
        provider="fixture",
        profile_id="fixture-v1",
        now=when,
    )


def _unresolved(ledger, attempt_id: str, *, uncertain: bool) -> None:
    reservation = ledger.reserve(
        operation_id=f"operation-{attempt_id}",
        attempt_id=attempt_id,
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal("0.10"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
        now=OLD,
    )
    if uncertain:
        ledger.mark_uncertain(
            reservation,
            price_snapshot={"fixture": True},
            provider="fixture",
            profile_id="fixture-v1",
            now=OLD,
        )


def _cache_row(storage, key: str, *, pinned: bool) -> None:
    cache_dir = storage.paths.artifacts / "property-cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    artifact = cache_dir / f"{key}.json"
    artifact.write_text(json.dumps({"key": key}), encoding="utf-8")
    with storage.state_engine.begin() as connection:
        connection.execute(
            insert(property_cache).values(
                cache_key=key,
                request_json="{}",
                connector_version="fixture-v1",
                response_artifact=artifact.relative_to(storage.paths.root).as_posix(),
                status="complete",
                is_complete=True,
                fetched_at=OLD,
                expires_at=OLD,
                stale_until=OLD,
                last_accessed_at=OLD,
                size_bytes=artifact.stat().st_size,
                pinned=pinned,
            )
        )


def test_retention_preview_and_apply_preserve_accounting_invariants(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    ledger = UsageLedger(storage)
    jobs_service = JobService(storage.state_engine)
    old_job = jobs_service.create("answer", "old-terminal")
    paused_job = jobs_service.create("corpus_update", "paused")
    with storage.state_engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == old_job.id)
            .values(state="succeeded", updated_at=OLD)
        )
        connection.execute(
            update(jobs)
            .where(jobs.c.id == paused_job.id)
            .values(state="paused", updated_at=OLD)
        )
    _settled(ledger, "old-settled", OLD)
    _settled(ledger, "current-settled", NOW)
    _unresolved(ledger, "old-reserved", uncertain=False)
    _unresolved(ledger, "old-uncertain", uncertain=True)
    _cache_row(storage, "old-unpinned", pinned=False)
    _cache_row(storage, "old-pinned", pinned=True)
    old_log = context.paths.logs / "old.log"
    old_log.write_text("redacted old diagnostic\n", encoding="utf-8")
    old_timestamp = OLD.timestamp()
    os.utime(old_log, (old_timestamp, old_timestamp))
    new_log = context.paths.logs / "new.log"
    new_log.write_text("new diagnostic\n", encoding="utf-8")

    try:
        service = WorkspaceRetentionService(storage, context.settings)
        preview = service.run(now=NOW)
        assert preview.applied is False
        assert preview.candidate_jobs == 1
        assert preview.candidate_usage_attempts == 1
        assert preview.candidate_usage_events == 2
        assert preview.candidate_cache_entries == 1
        assert preview.candidate_cache_artifacts == 1
        assert preview.candidate_log_files == 1
        assert preview.protected_active_jobs == 1
        assert preview.protected_unresolved_usage_attempts == 2
        assert preview.protected_current_month_usage_attempts == 1
        assert preview.protected_pinned_cache_entries == 1
        assert old_log.exists()

        result = service.run(apply=True, now=NOW)
        assert result.removed_jobs == 1
        assert result.removed_usage_attempts == 1
        assert result.removed_usage_events == 2
        assert result.removed_cache_entries == 1
        assert result.removed_cache_artifacts == 1
        assert result.removed_log_files == 1
        assert result.file_delete_failures == 0
        assert result.usage_history_pruned_before is not None

        with storage.state_engine.connect() as connection:
            remaining_attempts = set(
                connection.scalars(select(usage_events.c.attempt_id))
            )
            remaining_cache = set(
                connection.scalars(select(property_cache.c.cache_key))
            )
            assert connection.scalar(
                select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
            ) is False
        assert remaining_attempts == {
            "current-settled",
            "old-reserved",
            "old-uncertain",
        }
        assert remaining_cache == {"old-pinned"}
        assert not old_log.exists()
        assert new_log.exists()
        usage = ledger.summary(
            monthly_cap_usd=Decimal("15"),
            timezone="America/New_York",
            now=NOW,
        )
        assert usage.settled_usd == Decimal("0.05000000")
        assert usage.history_pruned_before == result.usage_history_pruned_before
    finally:
        storage.close()


def test_retention_apply_refuses_active_job_without_deleting_candidates(
    tmp_path,
) -> None:
    context, storage = _workspace(tmp_path)
    old_job = JobService(storage.state_engine).create("answer", "old-terminal")
    with storage.state_engine.begin() as connection:
        connection.execute(
            update(jobs)
            .where(jobs.c.id == old_job.id)
            .values(state="succeeded", updated_at=OLD)
        )
    JobService(storage.state_engine).create("answer", "active")
    try:
        with pytest.raises(RetentionError, match="active jobs"):
            WorkspaceRetentionService(storage, context.settings).run(
                apply=True, now=NOW
            )
        with storage.state_engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(jobs)) == 2
    finally:
        storage.close()


def test_retention_apply_refuses_active_paid_call(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    reservation = UsageLedger(storage).reserve(
        operation_id="active-operation",
        attempt_id="active-attempt",
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal("0.10"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
        now=NOW,
    )
    try:
        with pytest.raises(RetentionError, match="provider calls"):
            WorkspaceRetentionService(storage, context.settings).run(
                apply=True, now=NOW
            )
        UsageLedger(storage).settle(
            reservation,
            actual_usd=Decimal("0.05"),
            input_tokens=1,
            output_tokens=1,
            price_snapshot={"fixture": True},
            provider="fixture",
            profile_id="fixture-v1",
            now=NOW,
        )
    finally:
        storage.close()
