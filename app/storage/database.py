from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, insert, select, text
from sqlalchemy.engine import Connection

from app.storage.schema import (
    CORPUS_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    corpus_metadata,
    corpus_schema_metadata,
    corpus_state,
    maintenance_state,
    state_metadata,
    state_schema_metadata,
)
from app.workspace.paths import WorkspacePaths


class SchemaVersionError(RuntimeError):
    pass


@dataclass(frozen=True)
class LocalStorage:
    paths: WorkspacePaths
    corpus_engine: Engine
    state_engine: Engine

    @classmethod
    def open(cls, paths: WorkspacePaths, *, initialize: bool = False) -> LocalStorage:
        if initialize:
            paths.create()
        corpus_engine = _sqlite_engine(paths.corpus_database)
        state_engine = _sqlite_engine(paths.state_database)
        storage = cls(
            paths=paths,
            corpus_engine=corpus_engine,
            state_engine=state_engine,
        )
        if initialize:
            storage.initialize()
        return storage

    def initialize(self) -> None:
        self.paths.create()
        _initialize_schema(
            self.corpus_engine,
            corpus_metadata,
            corpus_schema_metadata,
            CORPUS_SCHEMA_VERSION,
            "corpus",
        )
        with self.corpus_engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
                        chunk_id UNINDEXED,
                        generation_id UNINDEXED,
                        title,
                        citation,
                        body,
                        tokenize = 'unicode61 remove_diacritics 2'
                    )
                    """
                )
            )
            state_exists = connection.scalar(
                select(corpus_state.c.id).where(corpus_state.c.id == 1)
            )
            if state_exists is None:
                connection.execute(
                    insert(corpus_state).values(id=1, active_generation_id=None)
                )
        _initialize_schema(
            self.state_engine,
            state_metadata,
            state_schema_metadata,
            STATE_SCHEMA_VERSION,
            "state",
        )
        with self.state_engine.begin() as connection:
            barrier_exists = connection.scalar(
                select(maintenance_state.c.id).where(maintenance_state.c.id == 1)
            )
            if barrier_exists is None:
                connection.execute(
                    insert(maintenance_state).values(
                        id=1, active=False, operation=None, started_at=None
                    )
                )
        _write_workspace_manifest(self.paths)

    def versions(self) -> dict[str, int | None]:
        return {
            "corpus": _read_schema_version(self.corpus_engine, corpus_schema_metadata),
            "state": _read_schema_version(self.state_engine, state_schema_metadata),
        }

    def assert_compatible(self) -> None:
        versions = self.versions()
        supported = {
            "corpus": CORPUS_SCHEMA_VERSION,
            "state": STATE_SCHEMA_VERSION,
        }
        if versions != supported:
            raise SchemaVersionError(
                "Local storage has incompatible schema versions "
                f"{versions}; this application supports {supported}. "
                "Run `nyc-housing migrate preflight` for the required action."
            )

    def close(self) -> None:
        self.corpus_engine.dispose()
        self.state_engine.dispose()


def _sqlite_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite+pysqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA secure_delete=ON")
        cursor.close()

    return engine


def _initialize_schema(
    engine: Engine,
    metadata,
    version_table,
    supported_version: int,
    name: str,
) -> None:
    existing = _read_schema_version(engine, version_table)
    if existing is not None and existing != supported_version:
        raise SchemaVersionError(
            f"Unsupported {name} schema version {existing}; "
            f"this application supports {supported_version}."
        )
    metadata.create_all(engine)
    with engine.begin() as connection:
        current = connection.scalar(
            select(version_table.c.value).where(version_table.c.key == "version")
        )
        if current is None:
            connection.execute(
                insert(version_table).values(
                    key="version", value=str(supported_version)
                )
            )


def _read_schema_version(engine: Engine, version_table) -> int | None:
    if not engine.url.database or not Path(engine.url.database).exists():
        return None
    try:
        with engine.connect() as connection:
            if not _table_exists(connection, version_table.name):
                return None
            value = connection.scalar(
                select(version_table.c.value).where(version_table.c.key == "version")
            )
    except Exception as exc:
        raise SchemaVersionError(
            f"Could not read schema version from {engine.url.database}."
        ) from exc
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise SchemaVersionError(
            f"Invalid schema version {value!r} in {engine.url.database}."
        ) from exc


def _table_exists(connection: Connection, table_name: str) -> bool:
    row = connection.scalar(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table_name"),
        {"table_name": table_name},
    )
    return row is not None


def _write_workspace_manifest(paths: WorkspacePaths) -> None:
    payload = {
        "format_version": 1,
        "corpus_schema_version": CORPUS_SCHEMA_VERSION,
        "state_schema_version": STATE_SCHEMA_VERSION,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        dir=paths.root,
        prefix=".workspace.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, paths.manifest)
    finally:
        temporary_path.unlink(missing_ok=True)
