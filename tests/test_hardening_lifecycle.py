import os
import sqlite3
import subprocess
import sys
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text, update

from app.corpus.bundle import CanonicalBundleService
from app.corpus.indexing import CorpusEmbeddingIndexer
from app.corpus.resources import ResourceMetadata
from app.corpus.service import CorpusService, SourceArtifact
from app.jobs.resources import ResourceJobs
from app.jobs.service import JobConflict, JobService, JobState
from app.maintenance.backup import BackupError, WorkspaceBackupService, restore_backup
from app.maintenance.retention import WorkspaceRetentionService
from app.providers.gateway import ProviderGateway
from app.providers.profiles import get_profile
from app.research.matters import MatterService
from app.security.local_session import LocalSessionService
from app.storage.database import LocalStorage
from app.storage.migrations import migrate_workspace, migration_preflight
from app.storage.schema import jobs, maintenance_state
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.settings import LocalSettingsError
from tests.test_hardening_safety import workspace as workspace


def test_migration_refuses_a_workspace_with_a_running_launcher(workspace):
    from app.launcher import workspace_launch_lock

    context, _storage = workspace
    with workspace_launch_lock(context.paths.root):
        with pytest.raises(LocalSettingsError, match="already starting or running"):
            migrate_workspace(context)


def test_final_backup_bytes_are_sanitized_and_restore_accepts_work(workspace, tmp_path):
    context, storage = workspace
    sessions = LocalSessionService(storage)
    sessions.exchange(sessions.begin_process())
    archive = tmp_path / "backup.zip"
    WorkspaceBackupService(storage).create(archive)
    database = tmp_path / "archived.sqlite3"
    with zipfile.ZipFile(archive) as zipped:
        database.write_bytes(zipped.read("databases/state.sqlite3"))
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT active FROM maintenance_state"
        ).fetchone() == (0,)
        for table in (
            "local_sessions",
            "launcher_tokens",
            "local_installation",
            "paid_call_leases",
        ):
            assert connection.execute(f'SELECT count(*) FROM "{table}"').fetchone() == (
                0,
            )
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    destination = tmp_path / "restored"
    restore_backup(archive, destination)
    restored = LocalStorage.open(
        WorkspaceContext.from_options(destination, environment={}).paths
    )
    try:
        job = JobService(restored.state_engine).create("answer", "new")
        JobService(restored.state_engine).request_cancel(job.id)
        WorkspaceBackupService(restored).create(tmp_path / "second-backup.zip")
    finally:
        restored.close()


def test_backup_temp_failure_releases_barrier(workspace, monkeypatch, tmp_path):
    _context, storage = workspace
    with monkeypatch.context() as patcher:
        patcher.setattr(
            "app.maintenance.backup.tempfile.mkdtemp",
            lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(OSError, match="disk full"):
            WorkspaceBackupService(storage).create(tmp_path / "backup.zip")
    with storage.state_engine.connect() as connection:
        assert connection.scalar(select(maintenance_state.c.active)) is False
    assert JobService(storage.state_engine).create("answer", "after-failure")


def test_crashed_barrier_recovers_but_live_barrier_cannot_be_stolen(
    workspace, tmp_path
):
    context, storage = workspace
    live = WorkspaceBackupService(storage)
    live._enter_barrier(datetime.now(UTC))
    try:
        with pytest.raises(JobConflict, match="maintenance"):
            JobService(storage.state_engine).create("answer", "blocked")
        with pytest.raises(BackupError):
            WorkspaceBackupService(storage).create(tmp_path / "blocked.zip")
    finally:
        live._leave_barrier()
    code = """
import os, sys
from datetime import UTC, datetime
from app.workspace.context import WorkspaceContext
from app.storage.database import LocalStorage
from app.maintenance.backup import WorkspaceBackupService
context = WorkspaceContext.from_options(sys.argv[1], environment={})
storage = LocalStorage.open(context.paths)
backup = WorkspaceBackupService(storage)
backup._enter_barrier(datetime.now(UTC))
os._exit(0)
"""
    subprocess.run(
        [sys.executable, "-c", code, str(context.paths.root)],
        check=True,
        cwd=Path(__file__).parents[1],
    )
    assert JobService(storage.state_engine).create("answer", "after-crash")


@pytest.mark.parametrize("unlink", [False, True])
def test_saved_item_delete_and_last_unlink_retire_receipts(workspace, unlink):
    _context, storage = workspace
    service = MatterService(storage)
    matter = service.create("Repairs")
    options = dict(
        kind="answer",
        payload={"answer": "Saved evidence"},
        source_identity="answer:one",
        idempotency_key="save-one",
    )
    item = service.save_payload(matter["id"], **options)
    result = service.delete_item(
        item["id"], apply=True, matter_id=matter["id"] if unlink else None
    )
    assert result["status"] == "deleted"
    assert service.get(matter["id"])["item_count"] == 0
    replacement = service.save_payload(matter["id"], **options)
    assert replacement["id"] != item["id"]


def test_overlapping_indexed_bundle_reuses_local_versions_and_vectors(
    workspace, tmp_path
):
    context, storage = workspace
    target_context = WorkspaceContext.from_options(
        tmp_path / "target", environment={}, initialize=True
    )
    target = LocalStorage.open(target_context.paths, initialize=True)
    try:
        CorpusService(target).install_artifacts(
            [
                SourceArtifact(
                    slug="ny-rpapl",
                    content="§ 711. Grounds for summary proceedings.".encode(),
                    source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                    content_type="text/plain",
                    retrieved_at=datetime.now(UTC),
                )
            ],
            allow_partial=True,
        )
        for selected_context, selected_storage in (
            (context, storage),
            (target_context, target),
        ):
            gateway = ProviderGateway(selected_context, UsageLedger(selected_storage))
            try:
                CorpusEmbeddingIndexer(selected_storage, gateway).index_active(
                    get_profile("fake-small-16"), credential=None, approve_cost=False
                )
            finally:
                gateway.close()
        with target.corpus_engine.connect() as connection:
            original_uri = connection.scalar(
                text("SELECT artifact_uri FROM source_versions")
            )
        bundle = tmp_path / "update.bundle"
        CanonicalBundleService(storage).export(bundle)
        imported = CanonicalBundleService(target).import_bundle(
            bundle, allow_partial=True
        )
        assert (
            CorpusService(target).verify(imported.generation_id)["status"] == "active"
        )
        with target.corpus_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM embeddings")) == 1
            assert (
                connection.scalar(text("SELECT artifact_uri FROM source_versions"))
                == original_uri
            )
    finally:
        target.close()


def test_failed_and_queued_cancelled_imports_remove_staging(workspace, monkeypatch):
    context, storage = workspace
    runner = ResourceJobs(context, storage)
    try:
        failed = runner.submit_add(
            b"private unsupported input",
            filename="private.bad",
            content_type=None,
            metadata=ResourceMetadata(title="Private"),
        )
        assert runner.wait(failed.id).state == JobState.FAILED
        assert list((context.paths.artifacts / "resource-staging").iterdir()) == []
        monkeypatch.setattr(runner, "_schedule", lambda *_: None)
        queued = runner.submit_add(
            b"private valid input",
            filename="private.txt",
            content_type=None,
            metadata=ResourceMetadata(title="Private"),
        )
        assert runner.cancel(queued.id).state == JobState.CANCELLED
        assert list((context.paths.artifacts / "resource-staging").iterdir()) == []
    finally:
        runner.close()


def test_paused_import_survives_backup_restore_with_its_input(
    workspace, monkeypatch, tmp_path
):
    context, storage = workspace
    runner = ResourceJobs(context, storage)
    monkeypatch.setattr(runner, "_schedule", lambda *_: None)
    job = runner.submit_add(
        b"Private repair notes.",
        filename="private.txt",
        content_type=None,
        metadata=ResourceMetadata(title="Private"),
    )
    jobs_service = JobService(storage.state_engine)
    jobs_service.claim(job.id, "interrupted", lease_seconds=1)
    jobs_service.recover_interrupted(now=datetime.now(UTC) + timedelta(seconds=2))
    assert jobs_service.get(job.id).state == JobState.PAUSED
    archive = tmp_path / "pending.zip"
    WorkspaceBackupService(storage).create(archive)
    destination = tmp_path / "resumed"
    restore_backup(archive, destination)
    restored_context = WorkspaceContext.from_options(destination, environment={})
    restored = LocalStorage.open(restored_context.paths)
    restored_runner = ResourceJobs(restored_context, restored)
    try:
        restored_runner.resume(job.id)
        assert restored_runner.wait(job.id).state == JobState.SUCCEEDED
        assert (
            list((restored_context.paths.artifacts / "resource-staging").iterdir())
            == []
        )
    finally:
        restored_runner.close()
        restored.close()
        runner.close()


def test_retention_prunes_orphan_stages_but_preserves_retryable_inputs(workspace):
    context, storage = workspace
    stage_root = context.paths.artifacts / "resource-staging"
    stage_root.mkdir(exist_ok=True)
    old = datetime.now(UTC) - timedelta(days=400)
    protected_id = str(uuid.uuid4())
    job = JobService(storage.state_engine).create(
        "resource_add", "corpus:core", resume={"stage_id": protected_id}
    )
    with storage.state_engine.begin() as connection:
        connection.execute(
            update(jobs).where(jobs.c.id == job.id).values(state="paused")
        )
    for stage_id in (protected_id, str(uuid.uuid4())):
        for suffix in (".bin", ".json"):
            path = stage_root / f"{stage_id}{suffix}"
            path.write_text("private draft")
            os.utime(path, (old.timestamp(), old.timestamp()))
    result = WorkspaceRetentionService(storage, context.settings).run(apply=True)
    assert result.removed_staging_files == 2
    assert {path.stem for path in stage_root.iterdir()} == {protected_id}


def test_schema_two_migration_rebuilds_shared_fts_and_keeps_backup(workspace):
    context, storage = workspace
    with storage.corpus_engine.begin() as connection:
        connection.execute(
            text("UPDATE schema_metadata SET value='2' WHERE key='version'")
        )
        connection.execute(
            text(
                "UPDATE chunk_fts SET generation_id="
                "(SELECT active_generation_id FROM corpus_state)"
            )
        )
    assert migration_preflight(context)["status"] == "migration_available"
    result = migrate_workspace(context)
    assert result.to_corpus_version == 3
    assert Path(result.backup_path).is_file()
    assert CorpusService(storage).verify()["status"] == "active"
    with storage.corpus_engine.connect() as connection:
        assert set(connection.scalars(text("SELECT generation_id FROM chunk_fts"))) == {
            "__shared__"
        }
