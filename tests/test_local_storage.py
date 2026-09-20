import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import insert, select, text

from app.storage.database import LocalStorage, SchemaVersionError
from app.storage.schema import state_schema_metadata, usage_events
from app.workspace.paths import resolve_workspace_paths


def test_local_storage_creates_separate_versioned_databases(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    try:
        assert storage.versions() == {"corpus": 3, "state": 2}
        assert paths.corpus_database.is_file()
        assert paths.state_database.is_file()
        manifest = json.loads(paths.manifest.read_text())
        assert manifest["corpus_schema_version"] == 3
        assert manifest["state_schema_version"] == 2
    finally:
        storage.close()


def test_foreign_keys_wal_and_fts5_are_enabled(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    try:
        with storage.corpus_engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
            fts = connection.scalar(
                text(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'chunk_fts'"
                )
            )
            assert fts == 1
        with storage.state_engine.connect() as connection:
            assert connection.scalar(text("PRAGMA foreign_keys")) == 1
            assert connection.scalar(text("PRAGMA journal_mode")) == "wal"
    finally:
        storage.close()


def test_workspaces_do_not_share_state(tmp_path) -> None:
    first_paths = resolve_workspace_paths(data_dir=tmp_path / "one", environment={})
    second_paths = resolve_workspace_paths(data_dir=tmp_path / "two", environment={})
    first = LocalStorage.open(first_paths, initialize=True)
    second = LocalStorage.open(second_paths, initialize=True)
    try:
        with first.state_engine.begin() as connection:
            connection.execute(
                insert(usage_events).values(
                    id="event-one",
                    operation_id="operation-one",
                    attempt_id="attempt-one",
                    event_type="reserve",
                    provider="fake",
                    profile_id="fake",
                    amount_usd="1.0",
                    price_snapshot_json="{}",
                    status="reserved",
                    created_at=datetime(2026, 9, 14, tzinfo=UTC),
                )
            )
        with second.state_engine.connect() as connection:
            assert connection.scalar(select(usage_events.c.id)) is None
    finally:
        first.close()
        second.close()


def test_newer_schema_is_refused_without_modification(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path / "workspace", environment={})
    storage = LocalStorage.open(paths, initialize=True)
    with storage.state_engine.begin() as connection:
        connection.execute(
            state_schema_metadata.update()
            .where(state_schema_metadata.c.key == "version")
            .values(value="999")
        )
    storage.close()

    reopened = LocalStorage.open(paths)
    try:
        with pytest.raises(SchemaVersionError, match="Unsupported state schema"):
            reopened.initialize()
        with reopened.state_engine.connect() as connection:
            assert (
                connection.scalar(
                    select(state_schema_metadata.c.value).where(
                        state_schema_metadata.c.key == "version"
                    )
                )
                == "999"
            )
    finally:
        reopened.close()
