from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from app.maintenance.backup import (
    MAX_BACKUP_BYTES,
    WorkspaceBackupService,
    _copy_and_hash,
    _read_backup,
    _sqlite_backup,
)
from app.maintenance.barrier import MaintenanceBarrier
from app.storage.database import (
    LocalStorage,
    SchemaVersionError,
    _write_workspace_manifest,
)
from app.storage.schema import (
    CORPUS_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    state_metadata,
)
from app.workspace.context import WorkspaceContext
from app.workspace.durable import sync_directory, sync_file, write_json_atomic
from app.workspace.locks import FileLease, LockBusy

RECOVERY_DIRECTORY = ".migration-recovery"
CURRENT_VERSIONS = {"corpus": CORPUS_SCHEMA_VERSION, "state": STATE_SCHEMA_VERSION}
MIGRATABLE_VERSIONS = (
    {"corpus": 1, "state": 1},
    {"corpus": 1, "state": 2},
    {"corpus": 2, "state": 1},
    {"corpus": 2, "state": 2},
    {"corpus": 3, "state": 1},
)


@dataclass(frozen=True)
class MigrationResult:
    status: str
    from_corpus_version: int
    to_corpus_version: int
    from_state_version: int
    to_state_version: int
    backup_path: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class MigrationFailure(SchemaVersionError):
    def __init__(self, message: str, backup_path: Path, *, recovered: bool) -> None:
        self.backup_path = backup_path
        self.recovered = recovered
        recovery = (
            "The previous workspace databases were restored."
            if recovered
            else "Automatic recovery failed. Restore the backup to a new folder."
        )
        super().__init__(f"Upgrade failed: {message} {recovery} Backup: {backup_path}")


def migration_preflight(context: WorkspaceContext) -> dict[str, object]:
    if not context.initialized:
        raise SchemaVersionError("Workspace is not initialized; run setup first.")
    if (context.paths.root / RECOVERY_DIRECTORY).exists():
        journal = _read_journal(context)
        return {
            "status": "recovery_required",
            "read_only": True,
            "recovery_required": True,
            "recovery_phase": journal["phase"],
            "backup_path": str(context.paths.root / journal["backup"]),
            "current_schema_versions": None,
            "supported_schema_versions": CURRENT_VERSIONS,
            "migration_required": True,
            "migration_available": False,
            "backup_required_before_migration": True,
            "next_action": "Run `nyc-housing migrate recover`, then preflight again.",
        }
    storage = LocalStorage.open(context.paths)
    try:
        versions = storage.versions()
    finally:
        storage.close()
    supported = CURRENT_VERSIONS
    compatible = versions == supported
    migration_available = versions in MIGRATABLE_VERSIONS
    if compatible:
        next_action = "No local schema migration is required."
    elif migration_available:
        next_action = "Run `nyc-housing migrate apply` to create a backup and upgrade."
    else:
        next_action = (
            "Keep the current code and workspace. This release has no reviewed "
            "migration path for these schema versions."
        )
    return {
        "status": (
            "compatible"
            if compatible
            else "migration_available"
            if migration_available
            else "migration_unavailable"
        ),
        "read_only": True,
        "current_schema_versions": versions,
        "supported_schema_versions": supported,
        "migration_required": not compatible,
        "migration_available": migration_available,
        "backup_required_before_migration": not compatible,
        "next_action": next_action,
    }


def migrate_workspace(context: WorkspaceContext) -> MigrationResult:
    from app.launcher import workspace_launch_lock

    with workspace_launch_lock(context.paths.root):
        return migrate_workspace_locked(context)


def migrate_workspace_locked(context: WorkspaceContext) -> MigrationResult:
    """Upgrade while the caller holds the launch lock for the entire operation.

    The desktop launcher already owns that lock; CLI callers use migrate_workspace.
    """
    if not context.initialized:
        raise SchemaVersionError("Workspace is not initialized; run setup first.")
    if (context.paths.root / RECOVERY_DIRECTORY).exists():
        recovery = recover_workspace_locked(context)
        if recovery["status"] == "upgrade_completed":
            return MigrationResult(
                status="upgrade_completed",
                from_corpus_version=recovery["from_schema_versions"]["corpus"],
                to_corpus_version=CORPUS_SCHEMA_VERSION,
                from_state_version=recovery["from_schema_versions"]["state"],
                to_state_version=STATE_SCHEMA_VERSION,
                backup_path=recovery["backup_path"],
            )
    storage = LocalStorage.open(context.paths)
    try:
        versions = storage.versions()
        if versions == CURRENT_VERSIONS:
            raise SchemaVersionError("Workspace already uses the current schema.")
        if versions not in MIGRATABLE_VERSIONS:
            raise SchemaVersionError(
                f"No migration is available for schema versions {versions}."
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        from_corpus = int(versions["corpus"])
        from_state = int(versions["state"])
        backup_path = context.paths.backups / (
            f"pre-migration-c{from_corpus}-s{from_state}-{timestamp}-"
            f"{uuid.uuid4().hex[:8]}.zip"
        )
        WorkspaceBackupService(storage).create(backup_path)
        _read_backup(backup_path)
        barrier = MaintenanceBarrier(storage.state_engine)
        barrier.enter("migration", datetime.now(UTC))
        failure = None
        journal = None
        try:
            # Publish verified, flushed snapshots and the journal before any DDL.
            journal = _prepare_recovery(context, versions, backup_path)
            try:
                if from_corpus == 1:
                    _migrate_corpus_v1_to_v2(storage)
                if from_corpus <= 2:
                    _migrate_corpus_v2_to_v3(storage)
                if from_state == 1:
                    _migrate_state_v1_to_v2(storage)
                _write_workspace_manifest(context.paths)
                storage.assert_compatible()
                storage.close()
                _flush_databases(context)
            except Exception as exc:
                storage.close()
                try:
                    _restore_snapshots(context, journal)
                    _finish_journal(context, journal, "rolled_back")
                except Exception as recovery_error:
                    raise MigrationFailure(
                        f"{exc}; recovery error: {recovery_error}",
                        backup_path,
                        recovered=False,
                    ) from exc
                raise MigrationFailure(str(exc), backup_path, recovered=True) from exc
            _finish_journal(context, journal, "committed")
        except BaseException as exc:
            failure = exc
            raise
        finally:
            try:
                barrier.leave()
                if journal and journal["phase"] in {"committed", "rolled_back"}:
                    _flush_databases(context)
                    _cleanup_recovery(context)
            except Exception as cleanup_error:
                if failure is None:
                    raise MigrationFailure(
                        f"Could not finish maintenance: {cleanup_error}",
                        backup_path,
                        recovered=False,
                    ) from cleanup_error
                failure.add_note(f"Maintenance cleanup also failed: {cleanup_error}")
        return MigrationResult(
            status="migrated",
            from_corpus_version=from_corpus,
            to_corpus_version=CORPUS_SCHEMA_VERSION,
            from_state_version=from_state,
            to_state_version=STATE_SCHEMA_VERSION,
            backup_path=str(backup_path),
        )
    finally:
        storage.close()


def _read_journal(context: WorkspaceContext) -> dict:
    directory = context.paths.root / RECOVERY_DIRECTORY
    try:
        path = directory / "journal.json"
        if directory.is_symlink() or path.is_symlink() or path.stat().st_size > 16_384:
            raise ValueError("unsafe recovery journal")
        journal = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(journal, dict)
            or journal.get("format_version") != 1
            or journal.get("phase") not in {"pending", "committed", "rolled_back"}
            or journal.get("from_versions") not in MIGRATABLE_VERSIONS
            or journal.get("to_versions") != CURRENT_VERSIONS
        ):
            raise ValueError("unsupported recovery journal")
        backup = Path(journal["backup"])
        if (
            backup.is_absolute()
            or len(backup.parts) != 2
            or backup.parts[0] != "backups"
            or ".." in backup.parts
            or (context.paths.root / backup).resolve().parent
            != context.paths.backups.resolve()
        ):
            raise ValueError("unsafe recovery backup path")
        if set(journal["snapshots"]) != {"corpus.sqlite3", "state.sqlite3"}:
            raise ValueError("incomplete recovery snapshots")
        return journal
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise SchemaVersionError(
            "The upgrade recovery journal is missing, damaged, or unsupported. "
            "Keep this workspace and restore a pre-migration backup to a new folder."
        ) from exc


def _file_metadata(path: Path) -> dict:
    with path.open("rb") as handle:
        return _copy_and_hash(handle, None, limit=MAX_BACKUP_BYTES)


def _prepare_recovery(context, versions, backup_path) -> dict:
    stage = Path(tempfile.mkdtemp(prefix=".migration-staging-", dir=context.paths.root))
    try:
        snapshots = {}
        for name in ("corpus.sqlite3", "state.sqlite3"):
            target = stage / name
            _sqlite_backup(context.paths.root / name, target)
            sync_file(target)
            snapshots[name] = _file_metadata(target)
        journal = {
            "format_version": 1,
            "phase": "pending",
            "from_versions": versions,
            "to_versions": CURRENT_VERSIONS,
            "backup": backup_path.relative_to(context.paths.root).as_posix(),
            "snapshots": snapshots,
        }
        write_json_atomic(stage / "journal.json", journal)
        os.replace(stage, context.paths.root / RECOVERY_DIRECTORY)
        sync_directory(context.paths.root)
        return journal
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _flush_databases(context: WorkspaceContext) -> None:
    for path in (context.paths.corpus_database, context.paths.state_database):
        with closing(sqlite3.connect(path)) as connection:
            if connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                raise RuntimeError(
                    "Another connection is using the workspace database."
                )
        sync_file(path)
    sync_directory(context.paths.root)


def _restore_snapshots(context: WorkspaceContext, journal: dict) -> None:
    directory = context.paths.root / RECOVERY_DIRECTORY
    # Check both snapshots fully before changing either live database.
    for name, metadata in journal["snapshots"].items():
        path = directory / name
        if path.is_symlink() or _file_metadata(path) != metadata:
            raise ValueError(f"Recovery snapshot verification failed: {name}")
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        ) as connection:
            version = connection.execute(
                "SELECT value FROM schema_metadata WHERE key='version'"
            ).fetchone()[0]
            if (
                int(version) != journal["from_versions"][name.split(".")[0]]
                or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
            ):
                raise ValueError(f"Invalid recovery snapshot: {name}")
    for name in journal["snapshots"]:
        _sqlite_backup(directory / name, context.paths.root / name)
    _write_workspace_manifest(context.paths, versions=journal["from_versions"])
    _flush_databases(context)


def _finish_journal(context: WorkspaceContext, journal: dict, phase: str) -> None:
    updated = {**journal, "phase": phase}
    write_json_atomic(context.paths.root / RECOVERY_DIRECTORY / "journal.json", updated)
    journal.update(updated)


def _cleanup_recovery(context: WorkspaceContext) -> None:
    # A crash during deletion must never expose a pending journal without snapshots.
    retired = context.paths.root / f".migration-complete-{uuid.uuid4().hex}"
    os.replace(context.paths.root / RECOVERY_DIRECTORY, retired)
    sync_directory(context.paths.root)
    shutil.rmtree(retired)
    sync_directory(context.paths.root)


def recover_workspace(context: WorkspaceContext) -> dict:
    from app.launcher import workspace_launch_lock

    with workspace_launch_lock(context.paths.root):
        return recover_workspace_locked(context)


def recover_workspace_locked(context: WorkspaceContext) -> dict:
    """Repeatable recovery; caller owns the launch lock through cleanup."""
    if not (context.paths.root / RECOVERY_DIRECTORY).exists():
        return {"status": "no_recovery_required"}
    journal = _read_journal(context)
    backup = context.paths.root / journal["backup"]
    lease = FileLease(context.paths.root / ".maintenance.lock")
    try:
        lease.acquire()
    except LockBusy as exc:
        raise SchemaVersionError(
            "Another workspace maintenance operation is active. "
            "Wait for it to finish before recovering the upgrade."
        ) from exc
    try:
        try:
            if journal["phase"] == "pending":
                _restore_snapshots(context, journal)
                _finish_journal(context, journal, "rolled_back")
            expected = (
                journal["to_versions"]
                if journal["phase"] == "committed"
                else journal["from_versions"]
            )
            storage = LocalStorage.open(context.paths, allow_pending_migration=True)
            try:
                if storage.versions() != expected:
                    raise ValueError(
                        "Recovered schema versions do not match the journal."
                    )
                with storage.state_engine.begin() as connection:
                    connection.execute(
                        text(
                            "UPDATE maintenance_state SET active=0, operation=NULL, "
                            "started_at=NULL WHERE id=1"
                        )
                    )
            finally:
                storage.close()
            _flush_databases(context)
            _cleanup_recovery(context)
        except Exception as exc:
            raise MigrationFailure(str(exc), backup, recovered=False) from exc
        return {
            "status": "upgrade_completed"
            if journal["phase"] == "committed"
            else "recovered",
            "schema_versions": expected,
            "from_schema_versions": journal["from_versions"],
            "backup_path": str(backup),
        }
    finally:
        lease.release()


def _migrate_corpus_v1_to_v2(storage: LocalStorage) -> None:
    statements = (
        "ALTER TABLE source_modules ADD COLUMN origin VARCHAR(40) "
        "NOT NULL DEFAULT 'core'",
        "ALTER TABLE source_modules ADD COLUMN acquisition_kind VARCHAR(40) "
        "NOT NULL DEFAULT 'managed_download'",
        "ALTER TABLE source_modules ADD COLUMN model_use_allowed BOOLEAN "
        "NOT NULL DEFAULT 1",
        "ALTER TABLE source_versions ADD COLUMN provenance_json TEXT "
        "NOT NULL DEFAULT '{}'",
        "ALTER TABLE chunks ADD COLUMN locator_json TEXT NOT NULL DEFAULT '{}'",
        """
        CREATE TABLE corpus_operations (
            operation_id VARCHAR(64) PRIMARY KEY NOT NULL,
            operation_type VARCHAR(80) NOT NULL,
            source_module_id VARCHAR(36),
            source_version_id VARCHAR(36),
            generation_id VARCHAR(36) NOT NULL,
            result_json TEXT NOT NULL,
            created_at DATETIME NOT NULL
        )
        """,
    )
    with storage.corpus_engine.begin() as connection:
        current = connection.scalar(
            text("SELECT value FROM schema_metadata WHERE key = 'version'")
        )
        if current != "1":
            raise SchemaVersionError(
                f"Corpus migration expected version 1, found {current!r}."
            )
        for statement in statements:
            connection.exec_driver_sql(statement)
        connection.execute(
            text("UPDATE schema_metadata SET value = '2' WHERE key = 'version'")
        )


def _migrate_state_v1_to_v2(storage: LocalStorage) -> None:
    with storage.state_engine.connect() as connection:
        current = connection.scalar(
            text("SELECT value FROM schema_metadata WHERE key = 'version'")
        )
    if current != "1":
        raise SchemaVersionError(
            f"State migration expected version 1, found {current!r}."
        )
    # Version 2 is additive. SQLAlchemy emits the reviewed table definitions and
    # leaves all existing state tables and records untouched.
    state_metadata.create_all(storage.state_engine)
    with storage.state_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS matter_fts USING fts5(
                    matter_id UNINDEXED,
                    item_id UNINDEXED,
                    title,
                    body,
                    tags,
                    tokenize = 'unicode61 remove_diacritics 2'
                )
                """
            )
        )
        connection.execute(
            text("UPDATE schema_metadata SET value = '2' WHERE key = 'version'")
        )


def _migrate_corpus_v2_to_v3(storage: LocalStorage) -> None:
    from app.corpus.service import CorpusService

    with storage.corpus_engine.begin() as connection:
        current = connection.scalar(
            text("SELECT value FROM schema_metadata WHERE key = 'version'")
        )
        if current != "2":
            raise SchemaVersionError(
                f"Corpus migration expected version 2, found {current!r}."
            )
        CorpusService(storage)._build_fts(connection, "")
        connection.execute(
            text("UPDATE schema_metadata SET value = '3' WHERE key = 'version'")
        )
