"""Native macOS window for the shared local browser application."""

from __future__ import annotations

import html
import sys
import threading
from typing import Any

from app.launcher import (
    LocalApplicationServer,
    prepare_sources,
    prepare_workspace,
    workspace_launch_lock,
)
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


class DesktopApplication:
    def __init__(self, context: WorkspaceContext, webview: Any) -> None:
        self.context = context
        self.webview = webview
        self.window = None
        self.runtime: LocalApplicationServer | None = None
        self.cancelled = threading.Event()
        self.bootstrap_started = threading.Event()
        self.bootstrap_done = threading.Event()
        self.application_loaded = False
        self.failed = False
        self._runtime_lock = threading.Lock()

    def run(self) -> int:
        self._configure_webview()
        launch_lock = workspace_launch_lock(self.context.paths.root)
        try:
            launch_lock.__enter__()
        except Exception as exc:
            self.failed = True
            self.window = self.webview.create_window(
                self.context.settings.app_name,
                html=_error_page(
                    "NYC Housing Research could not start",
                    str(exc),
                ),
                width=760,
                height=520,
                min_size=(640, 440),
                background_color="#f4f0e8",
                text_select=True,
            )
            self.webview.start(debug=False)
            return 3
        try:
            self.window = self.webview.create_window(
                self.context.settings.app_name,
                html=_status_page(
                    "Starting NYC Housing Research",
                    "Preparing your private workspace…",
                ),
                width=1280,
                height=820,
                min_size=(900, 650),
                background_color="#f4f0e8",
                text_select=True,
            )
            self.window.events.closed += self._on_closed
            self.webview.start(
                self._bootstrap,
                self.window,
                debug=False,
            )
        except Exception as exc:
            self.failed = True
            # The GUI may not have started yet, so keep a useful terminal message
            # for development builds and crash-report collection.
            print(f"NYC Housing Research could not start: {exc}", file=sys.stderr)
        finally:
            self.cancelled.set()
            self._stop_runtime()
            if self.bootstrap_started.is_set():
                self.bootstrap_done.wait(35)
            launch_lock.__exit__(None, None, None)
        return 3 if self.failed else 0

    def _configure_webview(self) -> None:
        self.webview.settings["ALLOW_DOWNLOADS"] = True
        self.webview.settings["ALLOW_FILE_URLS"] = False
        self.webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
        self.webview.settings["OPEN_DEVTOOLS_IN_DEBUG"] = False

    def _bootstrap(self, window) -> None:
        self.bootstrap_started.set()
        storage = None
        try:
            self._show_status("Preparing your workspace…")
            storage = prepare_workspace(self.context)
            storage.close()
            storage = None
            if self.cancelled.is_set():
                return

            self._show_status("Starting the private local service…")
            runtime = LocalApplicationServer(self.context)
            runtime.__enter__()
            with self._runtime_lock:
                self.runtime = runtime
            if self.cancelled.is_set():
                self._stop_runtime()
                return
            runtime.start_background()
            window.load_url(runtime.launch_url)
            self.application_loaded = True

            # Source installation is a normal durable job. Starting it after the
            # UI loads lets the existing Sources view show progress and recovery.
            if not self.cancelled.is_set():
                storage = LocalStorage.open(self.context.paths)
                prepare_sources(
                    self.context,
                    storage,
                    progress=self._show_status,
                    cancelled=self.cancelled.is_set,
                )
        except Exception as exc:
            self.failed = True
            print(f"NYC Housing Research could not start: {exc}", file=sys.stderr)
            if not self.application_loaded and not self.cancelled.is_set():
                window.load_html(
                    _error_page(
                        "NYC Housing Research could not start",
                        str(exc),
                    )
                )
        finally:
            if storage is not None:
                storage.close()
            self.bootstrap_done.set()

    def _show_status(self, message: str) -> None:
        print(message, flush=True)
        if (
            self.window is not None
            and not self.application_loaded
            and not self.cancelled.is_set()
        ):
            self.window.load_html(
                _status_page("Starting NYC Housing Research", message.strip())
            )

    def _on_closed(self, *_args) -> None:
        self.cancelled.set()

    def _stop_runtime(self) -> None:
        with self._runtime_lock:
            runtime = self.runtime
            self.runtime = None
        if runtime is not None:
            runtime.__exit__(None, None, None)


def _page(title: str, message: str, *, error: bool) -> str:
    safe_title = html.escape(title)
    safe_message = html.escape(message)
    accent = "#a33b20" if error else "#d5a800"
    label = "Startup problem" if error else "Private local application"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont,
      "Segoe UI", sans-serif; background: #f4f0e8; color: #172a3a; }}
    body {{ min-height: 100vh; margin: 0; display: grid; place-items: center; }}
    main {{ width: min(520px, calc(100vw - 64px)); padding: 36px;
      background: #fffdf8; border: 1px solid #d9d2c5; border-radius: 18px;
      box-shadow: 0 18px 55px rgba(23, 42, 58, .12); }}
    .mark {{ width: 44px; height: 44px; border-radius: 11px; background: #172a3a;
      display: grid; place-items: center; color: #f0c808; font-weight: 800;
      margin-bottom: 28px; border-bottom: 5px solid {accent}; }}
    .eyebrow {{ color: #68747d; font-size: 12px; font-weight: 700;
      letter-spacing: .1em; text-transform: uppercase; }}
    h1 {{ margin: 10px 0 12px; font-family: Georgia, serif; font-size: 29px; }}
    p {{ margin: 0; color: #53616b; font-size: 16px; line-height: 1.55; }}
    .pulse {{ display: inline-block; width: 8px; height: 8px; margin-right: 8px;
      border-radius: 50%; background: {accent}; animation: pulse 1.3s infinite; }}
    @keyframes pulse {{ 50% {{ opacity: .3; transform: scale(.8); }} }}
  </style>
</head>
<body><main>
  <div class="mark" aria-hidden="true">NYC</div>
  <div class="eyebrow">{label}</div>
  <h1>{safe_title}</h1>
  <p><span class="pulse" aria-hidden="true"></span>{safe_message}</p>
</main></body>
</html>"""


def _status_page(title: str, message: str) -> str:
    return _page(title, message, error=False)


def _error_page(title: str, message: str) -> str:
    return _page(title, message, error=True)


def main() -> int:
    if sys.platform != "darwin":
        print("The standalone desktop application currently supports macOS only.")
        return 2
    try:
        import webview
    except ImportError:
        print(
            "Desktop support is not installed. Install the desktop dependency extra.",
            file=sys.stderr,
        )
        return 3
    context = WorkspaceContext.from_options()
    return DesktopApplication(context, webview).run()


if __name__ == "__main__":
    raise SystemExit(main())
