import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.corpus.service import CorpusService, SourceArtifact
from app.maintenance.backup import BackupError, WorkspaceBackupService, restore_backup
from app.retrieval.local import LocalSearch
from app.security.local_session import LocalSessionService
from app.storage.database import LocalStorage
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext


def _prepared(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "source", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings.",
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    reservation = UsageLedger(storage).reserve(
        operation_id="operation",
        attempt_id="attempt",
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal("0.10"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
    )
    UsageLedger(storage).settle(
        reservation,
        actual_usd=Decimal("0.05"),
        input_tokens=1,
        output_tokens=1,
        price_snapshot={"fixture": True},
        provider="fixture",
        profile_id="fixture-v1",
    )
    return context, storage


def test_sanitized_backup_restores_corpus_and_usage_to_new_workspace(tmp_path) -> None:
    context, storage = _prepared(tmp_path)
    launch_token = LocalSessionService(storage).begin_process()
    credential = "sk-private-fixture-value"
    (context.paths.root / "credentials.json").write_text(
        json.dumps({"openai": credential}), encoding="utf-8"
    )
    (context.paths.root / "credentials.json").chmod(0o600)
    archive = tmp_path / "workspace-backup.nychousing-backup"
    try:
        summary = WorkspaceBackupService(storage).create(archive)
        assert summary.secrets_included is False
    finally:
        storage.close()

    with zipfile.ZipFile(archive) as backup:
        names = backup.namelist()
        content = b"".join(backup.read(name) for name in names)
    assert "credentials.json" not in names
    assert credential.encode() not in content
    assert launch_token.encode() not in content

    destination = tmp_path / "restored"
    restore_backup(archive, destination)
    restored_context = WorkspaceContext.from_options(destination, environment={})
    assert restored_context.settings.workspace_id != context.settings.workspace_id
    restored = LocalStorage.open(restored_context.paths)
    try:
        result = LocalSearch(restored).search("RPAPL 711")
        assert result.results[0].citation == "RPAPL § 711"
        usage = UsageLedger(restored).summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert usage.settled_usd == Decimal("0.05000000")
    finally:
        restored.close()


def test_corrupt_backup_does_not_create_destination(tmp_path) -> None:
    context, storage = _prepared(tmp_path)
    archive = tmp_path / "valid.backup"
    try:
        WorkspaceBackupService(storage).create(archive)
    finally:
        storage.close()
    corrupt = tmp_path / "corrupt.backup"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "databases/corpus.sqlite3":
                content += b"corrupt"
            target.writestr(name, content)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(BackupError, match="verification"):
        restore_backup(corrupt, destination)
    assert not destination.exists()


def test_backup_refuses_active_jobs(tmp_path) -> None:
    _context, storage = _prepared(tmp_path)
    from app.jobs.service import JobService

    try:
        JobService(storage.state_engine).create("corpus_update", "core")
        with pytest.raises(BackupError, match="active jobs"):
            WorkspaceBackupService(storage).create(tmp_path / "blocked.backup")
    finally:
        storage.close()


def test_large_backup_roundtrip_uses_bounded_memory(tmp_path, monkeypatch):
    import hashlib
    import os
    import tracemalloc
    from pathlib import Path

    from sqlalchemy import select

    import app.maintenance.backup as backups
    from app.storage.schema import source_versions

    context, storage = _prepared(tmp_path)
    with storage.corpus_engine.connect() as connection:
        relative = connection.scalar(select(source_versions.c.artifact_uri))
    artifact = context.paths.root / relative
    block = os.urandom(1024**2)
    digest = hashlib.sha256()
    with artifact.open("wb") as handle:
        for _ in range(24):
            handle.write(block)
            digest.update(block)
    del block
    original_read_bytes = Path.read_bytes

    def no_whole_files(path):
        assert path.name == "settings.json", f"Whole-file read: {path}"
        return original_read_bytes(path)

    def no_zip_read(*args, **kwargs):
        pytest.fail("Archive members must be streamed")

    monkeypatch.setattr(Path, "read_bytes", no_whole_files)
    monkeypatch.setattr(zipfile.ZipFile, "read", no_zip_read)
    archive, restored = tmp_path / "large.zip", tmp_path / "restored-large"
    tracemalloc.start()
    try:
        WorkspaceBackupService(storage).create(archive)
        backups._read_backup(archive)
        restore_backup(archive, restored)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        storage.close()
    assert peak < 16 * 1024**2, f"Archive-sized allocation: {peak}"
    with (restored / relative).open("rb") as handle:
        assert hashlib.file_digest(handle, "sha256").hexdigest() == digest.hexdigest()


@pytest.mark.parametrize(
    "damage",
    [
        "duplicate",
        "missing_database",
        "outside_artifact",
        "unmapped_artifact",
        "bad_shape",
    ],
)
def test_invalid_backup_structure_never_publishes_restore(tmp_path, damage):
    _context, storage = _prepared(tmp_path)
    archive = tmp_path / "source.zip"
    try:
        WorkspaceBackupService(storage).create(archive)
    finally:
        storage.close()
    with zipfile.ZipFile(archive) as source:
        members = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(members["backup-manifest.json"])
    if damage == "missing_database":
        members.pop("databases/state.sqlite3")
        manifest["members"].pop("databases/state.sqlite3")
    elif damage == "outside_artifact":
        relative = next(iter(manifest["artifact_map"]))
        manifest["artifact_map"]["artifacts/../../outside"] = manifest[
            "artifact_map"
        ].pop(relative)
    elif damage == "unmapped_artifact":
        manifest["artifact_map"].clear()
    elif damage == "bad_shape":
        manifest = []
    members["backup-manifest.json"] = json.dumps(manifest).encode()
    corrupt = tmp_path / "invalid.zip"
    with zipfile.ZipFile(corrupt, "w") as target:
        for name, content in members.items():
            target.writestr(name, content)
        if damage == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate"):
                target.writestr("settings.json", b"{}")
    destination = tmp_path / "invalid-restore"
    with pytest.raises(BackupError):
        restore_backup(corrupt, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".invalid-restore-restore-*"))


def test_backup_write_failure_preserves_previous_archive_and_releases_barrier(
    tmp_path, monkeypatch
):
    from sqlalchemy import text

    import app.maintenance.backup as backups

    _context, storage = _prepared(tmp_path)
    archive = tmp_path / "existing.zip"
    archive.write_bytes(b"previous backup")

    def disk_full(source, target, **kwargs):
        target.write(source.read(128))
        raise OSError("disk full")

    monkeypatch.setattr(backups, "_copy_and_hash", disk_full)
    try:
        with pytest.raises(OSError, match="disk full"):
            WorkspaceBackupService(storage).create(archive)
        with storage.state_engine.connect() as connection:
            assert not connection.scalar(text("SELECT active FROM maintenance_state"))
        assert archive.read_bytes() == b"previous backup"
        assert not list(tmp_path.glob(".backup-*.tmp"))
    finally:
        storage.close()


def test_backup_size_limits_apply_to_creation_and_restore(tmp_path, monkeypatch):
    import app.maintenance.backup as backups

    _context, storage = _prepared(tmp_path)
    archive = tmp_path / "valid.zip"
    try:
        WorkspaceBackupService(storage).create(archive)
        monkeypatch.setattr(backups, "MAX_BACKUP_BYTES", archive.stat().st_size + 1)
        # Compressed archive fits; expanded databases do not.
        with pytest.raises(BackupError, match="Expanded backup"):
            restore_backup(archive, tmp_path / "too-large")
        with pytest.raises(BackupError, match="supported size"):
            WorkspaceBackupService(storage).create(tmp_path / "new.zip")
        assert not (tmp_path / "too-large").exists()
        assert not (tmp_path / "new.zip").exists()
    finally:
        storage.close()
