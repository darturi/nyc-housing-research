from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import text

from app.cli.main import EXIT_INVALID_CONFIGURATION, main
from app.launcher import workspace_launch_lock
from app.storage.database import LocalStorage, MigrationRecoveryRequired
from app.storage.migrations import (
    MigrationFailure,
    migrate_workspace,
    migration_preflight,
    recover_workspace,
)
from app.workspace.context import WorkspaceContext
from app.workspace.settings import LocalSettingsError


@pytest.fixture
def workspace(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    with storage.corpus_engine.begin() as connection:
        connection.execute(text("UPDATE schema_metadata SET value='2'"))
    with storage.state_engine.begin() as connection:
        connection.execute(text("UPDATE schema_metadata SET value='1'"))
        connection.execute(text("CREATE TABLE saved_fixture (body TEXT)"))
        connection.execute(
            text("INSERT INTO saved_fixture VALUES ('preserve research')")
        )
    storage.close()
    return context


def crash_upgrade(context, point):
    code = """
import os, sys
from sqlalchemy import text
import app.storage.migrations as m
from app.workspace.context import WorkspaceContext
context = WorkspaceContext.from_options(sys.argv[1], environment={})
point = sys.argv[2]
if point == "partial_ddl":
    def crash(storage):
        with storage.corpus_engine.begin() as connection:
            connection.execute(text("CREATE TABLE interrupted_ddl (id INTEGER)"))
        os._exit(73)
    m._migrate_corpus_v2_to_v3 = crash
elif point == "between_stores":
    m._migrate_state_v1_to_v2 = lambda storage: os._exit(73)
elif point == "before_commit":
    m._finish_journal = lambda *args: os._exit(73)
elif point == "after_commit":
    m._cleanup_recovery = lambda *args: os._exit(73)
elif point == "during_cleanup":
    remove = m.shutil.rmtree
    def crash_cleanup(path, *args, **kwargs):
        if path.name.startswith(".migration-complete-"):
            os._exit(73)
        return remove(path, *args, **kwargs)
    m.shutil.rmtree = crash_cleanup
m.migrate_workspace(context)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(context.paths.root), point],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 73, result.stderr


def raw_versions(context):
    result = {}
    for name in ("corpus", "state"):
        with closing(
            sqlite3.connect(context.paths.root / f"{name}.sqlite3")
        ) as connection:
            result[name] = int(
                connection.execute(
                    "SELECT value FROM schema_metadata WHERE key='version'"
                ).fetchone()[0]
            )
    return result


@pytest.mark.parametrize(
    "point", ["partial_ddl", "between_stores", "before_commit", "after_commit"]
)
def test_process_exit_at_upgrade_boundaries_recovers_consistently(workspace, point):
    crash_upgrade(workspace, point)
    journal = workspace.paths.root / ".migration-recovery" / "journal.json"
    before = journal.read_bytes()
    assert migration_preflight(workspace)["status"] == "recovery_required"
    assert journal.read_bytes() == before
    with pytest.raises(MigrationRecoveryRequired):
        LocalStorage.open(workspace.paths)
    result = recover_workspace(workspace)
    committed = point == "after_commit"
    assert result["status"] == ("upgrade_completed" if committed else "recovered")
    assert raw_versions(workspace) == (
        {"corpus": 3, "state": 2} if committed else {"corpus": 2, "state": 1}
    )
    assert not journal.parent.exists()
    assert Path(result["backup_path"]).exists()
    storage = LocalStorage.open(workspace.paths)
    with storage.state_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT body FROM saved_fixture"))
            == "preserve research"
        )
        assert connection.scalar(text("SELECT active FROM maintenance_state")) == 0
    with storage.corpus_engine.connect() as connection:
        assert not connection.scalar(
            text("SELECT name FROM sqlite_master WHERE name='interrupted_ddl'")
        )
    storage.close()
    assert recover_workspace(workspace)["status"] == "no_recovery_required"
    if not committed:
        assert migrate_workspace(workspace).status == "migrated"


def test_recovery_itself_can_be_interrupted_and_repeated(workspace):
    crash_upgrade(workspace, "before_commit")
    code = """
import os, sys
import app.storage.migrations as m
from app.workspace.context import WorkspaceContext
context = WorkspaceContext.from_options(sys.argv[1], environment={})
copy = m._sqlite_backup
def interrupted_copy(source, destination):
    copy(source, destination)
    os._exit(74)
m._sqlite_backup = interrupted_copy
m.recover_workspace(context)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(workspace.paths.root)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 74, result.stderr
    assert raw_versions(workspace) == {"corpus": 2, "state": 2}
    assert recover_workspace(workspace)["status"] == "recovered"
    assert raw_versions(workspace) == {"corpus": 2, "state": 1}


def test_crash_during_cleanup_keeps_committed_workspace_usable(workspace):
    crash_upgrade(workspace, "during_cleanup")
    assert not (workspace.paths.root / ".migration-recovery").exists()
    storage = LocalStorage.open(workspace.paths)
    storage.assert_compatible()
    storage.close()
    assert recover_workspace(workspace)["status"] == "no_recovery_required"


def test_damaged_snapshot_blocks_recovery_before_either_database_changes(workspace):
    crash_upgrade(workspace, "before_commit")
    (workspace.paths.root / ".migration-recovery" / "state.sqlite3").write_bytes(
        b"damaged"
    )
    with pytest.raises(MigrationFailure, match="verification") as caught:
        recover_workspace(workspace)
    assert not caught.value.recovered
    assert caught.value.backup_path.is_file()
    assert raw_versions(workspace) == {"corpus": 3, "state": 2}
    with pytest.raises(MigrationRecoveryRequired):
        LocalStorage.open(workspace.paths, initialize=True)


def test_recovery_honors_live_launcher_and_maintenance_locks(workspace, capsys):
    from app.storage.database import SchemaVersionError
    from app.workspace.locks import FileLease

    crash_upgrade(workspace, "between_stores")
    with workspace_launch_lock(workspace.paths.root):
        with pytest.raises(LocalSettingsError, match="already starting"):
            recover_workspace(workspace)
    lease = FileLease(workspace.paths.root / ".maintenance.lock")
    lease.acquire()
    try:
        with pytest.raises(SchemaVersionError, match="maintenance operation is active"):
            recover_workspace(workspace)
        assert (
            main(
                [
                    "--data-dir",
                    str(workspace.paths.root),
                    "migrate",
                    "recover",
                    "--json",
                ]
            )
            == EXIT_INVALID_CONFIGURATION
        )
        assert "maintenance operation is active" in capsys.readouterr().err
    finally:
        lease.release()
    assert raw_versions(workspace) == {"corpus": 3, "state": 1}


def test_cli_preflight_setup_guard_and_explicit_recovery(workspace, capsys):
    crash_upgrade(workspace, "between_stores")
    settings_before = workspace.paths.config_file.read_bytes()
    base = ["--data-dir", str(workspace.paths.root)]
    assert main([*base, "migrate", "preflight", "--json"]) == EXIT_INVALID_CONFIGURATION
    assert json.loads(capsys.readouterr().out)["status"] == "recovery_required"
    assert main([*base, "setup", "--json"]) == EXIT_INVALID_CONFIGURATION
    capsys.readouterr()
    assert workspace.paths.config_file.read_bytes() == settings_before
    assert main([*base, "migrate", "recover", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "recovered"
    assert main([*base, "migrate", "apply", "--json"]) == 0


def test_cli_apply_recovers_before_retrying(workspace):
    crash_upgrade(workspace, "partial_ddl")
    assert migrate_workspace(workspace).status == "migrated"
    assert raw_versions(workspace) == {"corpus": 3, "state": 2}


def test_apply_finishes_committed_upgrade_without_rolling_it_back(workspace):
    crash_upgrade(workspace, "after_commit")
    assert migrate_workspace(workspace).status == "upgrade_completed"
    assert raw_versions(workspace) == {"corpus": 3, "state": 2}


@pytest.mark.parametrize("committed", [False, True])
def test_browser_launcher_recovers_before_preparing_workspace(workspace, committed):
    from app.launcher import start_workspace
    from app.storage.database import SchemaVersionError

    crash_upgrade(workspace, "after_commit" if committed else "between_stores")
    if committed:
        assert start_workspace(workspace, setup_only=True, skip_core=True) == 0
    else:
        with pytest.raises(SchemaVersionError, match="migrate preflight"):
            start_workspace(workspace, setup_only=True, skip_core=True)
        assert raw_versions(workspace) == {"corpus": 2, "state": 1}
    assert not (workspace.paths.root / ".migration-recovery").exists()


def test_damaged_journal_is_read_only_and_keeps_all_recovery_material(workspace):
    from app.storage.database import SchemaVersionError

    crash_upgrade(workspace, "before_commit")
    journal = workspace.paths.root / ".migration-recovery" / "journal.json"
    journal.write_text('{"broken": true}')
    before = {path.name: path.read_bytes() for path in journal.parent.iterdir()}
    with pytest.raises(SchemaVersionError, match="journal"):
        recover_workspace(workspace)
    assert before == {path.name: path.read_bytes() for path in journal.parent.iterdir()}
    assert raw_versions(workspace) == {"corpus": 3, "state": 2}
