from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import text

from app.maintenance.backup import WorkspaceBackupService
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


def migration_preflight(context: WorkspaceContext) -> dict[str, object]:
    if not context.initialized:
        raise SchemaVersionError("Workspace is not initialized; run setup first.")
    storage = LocalStorage.open(context.paths)
    try:
        versions = storage.versions()
    finally:
        storage.close()
    supported = {
        "corpus": CORPUS_SCHEMA_VERSION,
        "state": STATE_SCHEMA_VERSION,
    }
    compatible = versions == supported
    migration_available = versions in (
        {"corpus": 1, "state": 1},
        {"corpus": 1, "state": 2},
        {"corpus": 2, "state": 1},
    )
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
    if not context.initialized:
        raise SchemaVersionError("Workspace is not initialized; run setup first.")
    storage = LocalStorage.open(context.paths)
    try:
        versions = storage.versions()
        if versions == {
            "corpus": CORPUS_SCHEMA_VERSION,
            "state": STATE_SCHEMA_VERSION,
        }:
            raise SchemaVersionError("Workspace already uses the current schema.")
        if versions not in (
            {"corpus": 1, "state": 1},
            {"corpus": 1, "state": 2},
            {"corpus": 2, "state": 1},
        ):
            raise SchemaVersionError(
                f"No migration is available for schema versions {versions}."
            )
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        from_corpus = int(versions["corpus"])
        from_state = int(versions["state"])
        backup_path = context.paths.backups / (
            f"pre-migration-c{from_corpus}-s{from_state}-{timestamp}.zip"
        )
        WorkspaceBackupService(storage).create(backup_path)
        if from_corpus == 1:
            _migrate_corpus_v1_to_v2(storage)
        if from_state == 1:
            _migrate_state_v1_to_v2(storage)
        _write_workspace_manifest(context.paths)
        storage.assert_compatible()
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
