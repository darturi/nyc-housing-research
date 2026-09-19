from __future__ import annotations

from dataclasses import dataclass

from app.desktop import DesktopApplication
from app.workspace.context import WorkspaceContext


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

    def load_html(self, content: str) -> None:
        self.html = content

    def load_url(self, url: str) -> None:
        self.urls.append(url)


class FakeWebview:
    def __init__(self) -> None:
        self.settings = {}
        self.windows = []

    def create_window(self, _title, *, html, **_kwargs):
        window = FakeWindow(html)
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
    monkeypatch.setattr(
        "app.desktop.LocalStorage.open", lambda _paths: source_storage
    )
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
