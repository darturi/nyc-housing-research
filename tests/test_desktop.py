from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Thread

import pytest
from sqlalchemy import text

from app.desktop import DesktopApplication
from app.launcher import workspace_launch_lock
from app.maintenance.backup import BackupError, restore_backup
from app.storage.database import LocalStorage
from app.storage.migrations import MigrationFailure, migrate_workspace
from app.workspace.context import WorkspaceContext
from app.workspace.settings import LocalSettingsError


class EventHook:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeWindow:
    def __init__(self, html: str) -> None:
        self.html = html
        self.urls = []
        self.events = type("Events", (), {"closed": EventHook()})()
        self.confirmations = []
        self.confirm = True

    def load_html(self, content: str) -> None:
        self.html = content

    def load_url(self, url: str) -> None:
        self.urls.append(url)

    def create_confirmation_dialog(self, title, message):
        self.confirmations.append((title, message))
        return self.confirm


class FakeWebview:
    def __init__(self) -> None:
        self.settings = {}
        self.windows = []
        self.confirm = True

    def create_window(self, _title, *, html, **_kwargs):
        window = FakeWindow(html)
        window.confirm = self.confirm
        self.windows.append(window)
        return window

    def start(self, function=None, argument=None, **_kwargs) -> None:
        if function is not None:
            function(argument)


@dataclass
class FakeStorage:
    closed: bool = False

    def close(self) -> None:
        self.closed = True


class FakeRuntime:
    instances = []

    def __init__(self, _context) -> None:
        self.launch_url = "http://127.0.0.1:54321/#launch=desktop-test"
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def __enter__(self):
        return self

    def start_background(self) -> None:
        self.started = True

    def __exit__(self, *_args) -> None:
        self.stopped = True


def context(tmp_path):
    return WorkspaceContext.from_options(tmp_path / "desktop workspace", environment={})


def test_desktop_uses_shared_workspace_server_and_source_setup(tmp_path, monkeypatch):
    workspace = context(tmp_path)
    prepared = FakeStorage()
    source_storage = FakeStorage()
    source_calls = []
    FakeRuntime.instances = []
    monkeypatch.setattr("app.desktop.prepare_workspace", lambda _context: prepared)
    monkeypatch.setattr("app.desktop.LocalStorage.open", lambda _paths: source_storage)
    monkeypatch.setattr("app.desktop.LocalApplicationServer", FakeRuntime)
    monkeypatch.setattr(
        "app.desktop.prepare_sources",
        lambda *args, **kwargs: source_calls.append((args, kwargs)) or True,
    )

    webview = FakeWebview()
    result = DesktopApplication(workspace, webview).run()

    assert result == 0
    assert prepared.closed and source_storage.closed
    assert webview.windows[0].urls == [FakeRuntime.instances[0].launch_url]
    assert FakeRuntime.instances[0].started
    assert FakeRuntime.instances[0].stopped
    assert len(source_calls) == 1
    assert webview.settings["ALLOW_DOWNLOADS"] is True
    assert webview.settings["ALLOW_FILE_URLS"] is False
    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True


def test_desktop_displays_escaped_startup_failure(tmp_path, monkeypatch):
    workspace = context(tmp_path)
    monkeypatch.setattr(
        "app.desktop.prepare_workspace",
        lambda _context: (_ for _ in ()).throw(ValueError("bad <workspace>")),
    )
    webview = FakeWebview()

    result = DesktopApplication(workspace, webview).run()

    assert result == 3
    assert "bad &lt;workspace&gt;" in webview.windows[0].html
    assert "bad <workspace>" not in webview.windows[0].html


@pytest.fixture
def older_workspace(tmp_path):
    workspace = context(tmp_path)
    workspace.initialize()
    storage = LocalStorage.open(workspace.paths, initialize=True)
    with storage.corpus_engine.begin() as connection:
        connection.execute(text("UPDATE schema_metadata SET value='2'"))
    storage.close()
    return workspace


def _fake_services(monkeypatch):
    FakeRuntime.instances = []
    monkeypatch.setattr("app.desktop.LocalApplicationServer", FakeRuntime)
    monkeypatch.setattr("app.desktop.prepare_sources", lambda *a, **k: True)


def test_desktop_upgrades_under_its_launch_lock_and_keeps_a_backup(
    older_workspace, tmp_path, monkeypatch
):
    _fake_services(monkeypatch)
    webview = FakeWebview()
    assert DesktopApplication(older_workspace, webview).run() == 0
    assert len(webview.windows[0].confirmations) == 1
    assert FakeRuntime.instances[0].started
    storage = LocalStorage.open(older_workspace.paths)
    assert storage.versions() == {"corpus": 3, "state": 2}
    storage.close()
    (archive,) = older_workspace.paths.backups.glob("pre-migration-*.zip")
    destination = tmp_path / "restore-check"
    restore_backup(archive, destination)
    restored = LocalStorage.open(WorkspaceContext.from_options(destination).paths)
    assert restored.versions() == {"corpus": 2, "state": 2}
    restored.close()


def test_desktop_declining_upgrade_leaves_databases_unchanged(
    older_workspace, monkeypatch
):
    _fake_services(monkeypatch)
    webview = FakeWebview()
    webview.confirm = False
    assert DesktopApplication(older_workspace, webview).run() == 0
    storage = LocalStorage.open(older_workspace.paths)
    assert storage.versions() == {"corpus": 2, "state": 2}
    storage.close()
    assert not list(older_workspace.paths.backups.glob("*.zip"))
    assert not FakeRuntime.instances
    assert "Upgrade cancelled" in webview.windows[0].html


def test_desktop_unknown_schema_never_offers_upgrade(older_workspace, monkeypatch):
    _fake_services(monkeypatch)
    storage = LocalStorage.open(older_workspace.paths)
    with storage.corpus_engine.begin() as connection:
        connection.execute(text("UPDATE schema_metadata SET value='999'"))
    storage.close()
    webview = FakeWebview()
    assert DesktopApplication(older_workspace, webview).run() == 3
    assert not webview.windows[0].confirmations
    assert not FakeRuntime.instances
    assert "compatible release" in webview.windows[0].html


def test_backup_failure_prevents_desktop_upgrade(older_workspace, monkeypatch):
    _fake_services(monkeypatch)

    def fail_backup(*args, **kwargs):
        raise BackupError("backup disk full")

    monkeypatch.setattr(
        "app.storage.migrations.WorkspaceBackupService.create", fail_backup
    )
    webview = FakeWebview()
    assert DesktopApplication(older_workspace, webview).run() == 3
    storage = LocalStorage.open(older_workspace.paths)
    assert storage.versions() == {"corpus": 2, "state": 2}
    storage.close()
    assert "backup disk full" in webview.windows[0].html
    assert not FakeRuntime.instances


def test_partial_migration_failure_restores_both_databases_and_can_retry(
    older_workspace, monkeypatch
):
    import app.storage.migrations as migrations

    storage = LocalStorage.open(older_workspace.paths)
    with storage.state_engine.begin() as connection:
        connection.execute(text("UPDATE schema_metadata SET value='1'"))
    storage.close()
    original = migrations._migrate_state_v1_to_v2

    def fail_state(storage):
        # Corpus migration has already committed; simulate a partly applied DDL.
        assert storage.versions()["corpus"] == 3
        with storage.state_engine.begin() as connection:
            connection.execute(text("CREATE TABLE interrupted_ddl (id INTEGER)"))
        raise RuntimeError("injected state migration failure")

    monkeypatch.setattr(migrations, "_migrate_state_v1_to_v2", fail_state)
    with pytest.raises(MigrationFailure) as caught:
        migrate_workspace(older_workspace)
    assert caught.value.recovered
    assert caught.value.backup_path.is_file()
    storage = LocalStorage.open(older_workspace.paths)
    assert storage.versions() == {"corpus": 2, "state": 1}
    with storage.state_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM sqlite_master WHERE name='interrupted_ddl'")
            )
            == 0
        )
        assert connection.scalar(text("SELECT active FROM maintenance_state")) == 0
    storage.close()
    monkeypatch.setattr(migrations, "_migrate_state_v1_to_v2", original)
    assert migrate_workspace(older_workspace).status == "migrated"


def test_desktop_reports_recovered_migration_failure(older_workspace, monkeypatch):
    _fake_services(monkeypatch)

    def fail_migration(_storage):
        raise RuntimeError("injected upgrade failure")

    monkeypatch.setattr(
        "app.storage.migrations._migrate_corpus_v2_to_v3", fail_migration
    )
    webview = FakeWebview()
    assert DesktopApplication(older_workspace, webview).run() == 3
    assert "previous workspace databases were restored" in webview.windows[0].html
    assert not FakeRuntime.instances


def test_desktop_offers_separate_copy_if_automatic_recovery_fails(
    older_workspace, monkeypatch
):
    import app.storage.migrations as migrations

    _fake_services(monkeypatch)
    real_copy = migrations._sqlite_backup
    real_migrate = migrations._migrate_corpus_v2_to_v3

    def copy_except_restore(source, destination):
        if destination == older_workspace.paths.corpus_database:
            raise OSError("cannot restore original")
        return real_copy(source, destination)

    monkeypatch.setattr(migrations, "_sqlite_backup", copy_except_restore)

    def fail_migration(storage):
        if storage.paths.root == older_workspace.paths.root:
            raise RuntimeError("injected upgrade failure")
        real_migrate(storage)

    monkeypatch.setattr(migrations, "_migrate_corpus_v2_to_v3", fail_migration)
    webview = FakeWebview()
    app = DesktopApplication(older_workspace, webview)
    assert app.run() == 0
    assert len(webview.windows[0].confirmations) == 2
    (recovered,) = older_workspace.paths.root.parent.glob("*-recovered-*")
    storage = LocalStorage.open(WorkspaceContext.from_options(recovered).paths)
    assert storage.versions() == {"corpus": 3, "state": 2}
    storage.close()
    assert app.context.paths.root == recovered
    assert FakeRuntime.instances[0].started
    assert (older_workspace.paths.root / ".migration-recovery").is_dir()
    reopened = DesktopApplication(older_workspace, FakeWebview())
    assert reopened.run() == 0
    assert reopened.context.paths.root == recovered


def test_corrupt_final_backup_blocks_migration(older_workspace, monkeypatch):
    from app.maintenance.backup import WorkspaceBackupService

    real_create = WorkspaceBackupService.create

    def corrupt_backup(self, destination, **kwargs):
        result = real_create(self, destination, **kwargs)
        destination.write_bytes(b"corrupt archive")
        return result

    monkeypatch.setattr(WorkspaceBackupService, "create", corrupt_backup)
    with pytest.raises(BackupError):
        migrate_workspace(older_workspace)
    storage = LocalStorage.open(older_workspace.paths)
    assert storage.versions() == {"corpus": 2, "state": 2}
    storage.close()


def test_closing_window_keeps_launch_lock_until_upgrade_finishes(
    older_workspace, monkeypatch
):
    from app.storage.migrations import migrate_workspace_locked

    _fake_services(monkeypatch)
    upgrading, finish = Event(), Event()

    def held_upgrade(workspace):
        upgrading.set()
        assert finish.wait(5), "test did not release migration"
        return migrate_workspace_locked(workspace)

    class ClosingWebview(FakeWebview):
        def start(self, function=None, argument=None, **kwargs):
            Thread(target=function, args=(argument,), daemon=True).start()
            assert upgrading.wait(5), "upgrade did not start"
            # Returning simulates the user closing the native window.

    monkeypatch.setattr("app.desktop.migrate_workspace_locked", held_upgrade)
    app = DesktopApplication(older_workspace, ClosingWebview())
    results = []
    thread = Thread(target=lambda: results.append(app.run()), daemon=True)
    thread.start()
    try:
        assert upgrading.wait(5)
        assert app.cancelled.wait(5)
        assert thread.is_alive()
        with pytest.raises(LocalSettingsError, match="already starting or running"):
            with workspace_launch_lock(older_workspace.paths.root):
                pytest.fail("launch lock released while upgrading")
    finally:
        finish.set()
        thread.join(5)
    assert not thread.is_alive()
    assert results == [0]
    assert not FakeRuntime.instances
    with workspace_launch_lock(older_workspace.paths.root):
        storage = LocalStorage.open(older_workspace.paths)
        assert storage.versions() == {"corpus": 3, "state": 2}
        storage.close()


@pytest.mark.parametrize("accept", [False, True])
def test_desktop_detects_interrupted_upgrade_before_starting_service(
    older_workspace, monkeypatch, accept
):
    from tests.test_migration_recovery import crash_upgrade

    crash_upgrade(older_workspace, "before_commit")
    _fake_services(monkeypatch)
    webview = FakeWebview()
    webview.confirm = accept
    app = DesktopApplication(older_workspace, webview)
    assert app.run() == 0
    assert (
        webview.windows[0].confirmations[0][0]
        == "Recover interrupted workspace upgrade?"
    )
    pending = older_workspace.paths.root / ".migration-recovery"
    if accept:
        assert not pending.exists()
        assert FakeRuntime.instances[0].started
        assert len(webview.windows[0].confirmations) == 2
    else:
        assert pending.exists()
        assert not FakeRuntime.instances
        assert "Recovery cancelled" in webview.windows[0].html


def test_desktop_opens_backup_copy_when_crash_snapshots_are_damaged(
    older_workspace, monkeypatch
):
    from tests.test_migration_recovery import crash_upgrade

    crash_upgrade(older_workspace, "before_commit")
    pending = older_workspace.paths.root / ".migration-recovery"
    (pending / "state.sqlite3").write_bytes(b"damaged")
    _fake_services(monkeypatch)
    app = DesktopApplication(older_workspace, FakeWebview())
    assert app.run() == 0
    assert app.context.paths.root != older_workspace.paths.root
    assert pending.exists()
    assert (older_workspace.paths.root / "desktop-recovery.json").exists()
    assert FakeRuntime.instances[0].started


def test_missing_selected_recovery_never_opens_original_workspace(
    older_workspace, monkeypatch
):
    import json

    _fake_services(monkeypatch)
    (older_workspace.paths.root / "desktop-recovery.json").write_text(
        json.dumps(
            {
                "format_version": 1,
                "workspace": older_workspace.paths.root.name + "-recovered-deadbeef",
            }
        )
    )
    webview = FakeWebview()
    assert DesktopApplication(older_workspace, webview).run() == 3
    assert "selected recovery workspace cannot be opened" in webview.windows[0].html
    assert not FakeRuntime.instances
    with workspace_launch_lock(older_workspace.paths.root):
        pass
