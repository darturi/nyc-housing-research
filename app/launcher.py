"""First-run preparation and the lifetime of one local browser application."""

from __future__ import annotations

import errno
import os
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from app.corpus.download import SourceDownloadError
from app.corpus.service import CorpusService, CorpusValidationError
from app.jobs.maintenance import CorpusMaintenanceJobs
from app.jobs.service import JobConflict, JobState
from app.storage.database import LocalStorage, SchemaVersionError
from app.storage.schema import CORPUS_SCHEMA_VERSION, STATE_SCHEMA_VERSION
from app.workspace.context import WorkspaceContext
from app.workspace.settings import (
    LocalSettingsError,
    load_local_settings,
    save_local_settings,
)


@contextmanager
def workspace_launch_lock(root: Path) -> Iterator[None]:
    """OS locks are released on exit/crash; stale lock files are harmless."""
    root.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(root / ".launcher.lock", os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "r+b") as handle:
        if os.name == "nt":
            import msvcrt

            # Windows byte-range locking needs an existing byte.
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise LocalSettingsError(
                    "This workspace is already starting or running. Use its open "
                    "browser/terminal, or stop it with Ctrl+C before restarting."
                ) from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LocalSettingsError(
                    "This workspace is already starting or running. Use its open "
                    "browser/terminal, or stop it with Ctrl+C before restarting."
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_workspace(context: WorkspaceContext) -> LocalStorage:
    """Check existing schema pairs before any initializer can change them."""
    if sys.version_info[:2] != (3, 12):
        raise LocalSettingsError(
            "Python 3.12 is required. Start through the repository launcher."
        )
    with sqlite3.connect(":memory:") as probe:
        try:
            probe.execute("CREATE VIRTUAL TABLE search_probe USING fts5(body)")
        except sqlite3.OperationalError as exc:
            raise LocalSettingsError(
                "This Python installation lacks SQLite full-text search. "
                "Use the repository launcher with uv-managed Python 3.12."
            ) from exc
    existing = (
        context.paths.corpus_database.exists(),
        context.paths.state_database.exists(),
    )
    storage = LocalStorage.open(context.paths)
    try:
        if any(existing):
            if not all(existing) or storage.versions() != {
                "corpus": CORPUS_SCHEMA_VERSION,
                "state": STATE_SCHEMA_VERSION,
            }:
                raise SchemaVersionError(
                    "The existing workspace is incomplete or requires a different "
                    "schema version. Run `nyc-housing migrate preflight` to check "
                    "for a backed-up upgrade, or restore a compatible backup; "
                    "setup will not replace its databases."
                )
        else:
            storage.initialize()
        if not context.paths.config_file.exists():
            # A one-time `start --offline` flag must not disable future launches.
            save_local_settings(context.paths, load_local_settings(context.paths))
        return storage
    except BaseException:
        storage.close()
        raise


def prepare_sources(
    context: WorkspaceContext,
    storage: LocalStorage,
    *,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> bool:
    """Only an empty workspace needs automatic, free source installation."""
    report = progress or (lambda message: print(message, flush=True))
    is_cancelled = cancelled or (lambda: False)
    service = CorpusService(storage)
    status = service.status()
    if status.active_generation_id:
        report(
            f"Your installed sources are ready ({status.chunk_count} passages)."
            + (
                " This is a partial library; add sources in the browser."
                if status.is_partial
                else ""
            )
        )
        return True
    if context.settings.offline:
        report(
            "Offline mode: source downloads skipped. Install sources when online."
        )
        return True

    report(
        "Downloading five official publications for free local search. "
        "This contacts their publishers and makes no paid model requests."
    )
    runner = CorpusMaintenanceJobs(context, storage)
    job = None
    try:
        # Resume only the latest free whole-core installation. Never resume an
        # embedding/update/export job merely because someone opened the app.
        candidates = [
            item
            for item in runner.list()
            if item.job_type == "corpus_install"
            and item.resume.get("operation") == "install"
            and item.resume.get("source") is None
        ]
        previous = candidates[0] if candidates else None
        if (
            previous
            and previous.retryable
            and previous.state
            in {
                JobState.PAUSED,
                JobState.FAILED,
            }
        ):
            report("Retrying the unfinished source installation...")
            job = runner.resume(previous.id)
        else:
            job = runner.submit("install")
        last_progress = None
        while True:
            current = runner.get(job.id)
            progress = (current.stage, current.progress_current, current.progress_total)
            if progress != last_progress:
                if current.stage == "downloading_sources":
                    report(
                        f"  Downloading publication {current.progress_current + 1}"
                        f" of {current.progress_total or 5}..."
                    )
                elif current.stage == "parse_and_activate":
                    report("  Checking publications and building local search...")
                last_progress = progress
            if current.state not in {JobState.QUEUED, JobState.RUNNING}:
                break
            if is_cancelled():
                runner.cancel(job.id)
                runner.wait(job.id)
                return False
            time.sleep(0.2)
        completed = runner.wait(job.id)
        if completed.state != JobState.SUCCEEDED:
            raise SourceDownloadError(
                completed.error_message or "Source installation stopped."
            )
        verified = service.verify()
        report(f"Free source search is ready ({verified['chunk_count']} passages).")
        return True
    except (SourceDownloadError, CorpusValidationError, JobConflict) as exc:
        print(f"Source setup needs attention: {exc}", file=sys.stderr, flush=True)
        print(
            "Open Sources in the browser to retry or resume. You can also rerun "
            "this same start command. Existing data has been kept.",
            file=sys.stderr,
            flush=True,
        )
        return False
    except KeyboardInterrupt:
        if job is not None:
            current = runner.get(job.id)
            if current.state in {JobState.QUEUED, JobState.RUNNING}:
                print("Stopping source setup after the current download...", flush=True)
                runner.cancel(job.id)
                runner.wait(job.id)
        raise
    finally:
        runner.close()


def start_workspace(
    context: WorkspaceContext,
    *,
    skip_core: bool = False,
    setup_only: bool = False,
    no_browser: bool = False,
    port: int | None = None,
) -> int:
    if port is not None and not 1 <= port <= 65535:
        raise LocalSettingsError("Port must be between 1 and 65535.")
    with workspace_launch_lock(context.paths.root):
        print("[3/4] Preparing your local workspace and source library...", flush=True)
        storage = prepare_workspace(context)
        try:
            ready = True if skip_core else prepare_sources(context, storage)
        finally:
            storage.close()
        print("OpenAI is optional: add your key in the browser's Settings.", flush=True)
        if setup_only:
            print(
                "Setup finished."
                if ready
                else "Setup is incomplete; sources need attention.",
                flush=True,
            )
            return 0 if ready else 3
        print("[4/4] Opening NYC Housing Research...", flush=True)
        return serve_workspace(context, port=port, no_browser=no_browser)


@contextmanager
def bind_loopback(port: int, *, allow_fallback: bool) -> Iterator[socket.socket]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        # Prevent accidental Windows SO_REUSEADDR sharing with a second process.
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as exc:
            if not allow_fallback or exc.errno != errno.EADDRINUSE:
                raise LocalSettingsError(
                    f"Cannot use local port {port}. Close the other application or "
                    "run this command with --port followed by an unused port."
                ) from exc
            listener.bind(("127.0.0.1", 0))
            print(
                f"Port {port} is busy; using {listener.getsockname()[1]} instead.",
                flush=True,
            )
        listener.listen(128)
        yield listener


def _open_browser_or_print_code(url: str, token: str, *, open_browser: bool) -> None:
    print(f"Open NYC Housing Research: {url}", flush=True)
    print(
        "Keep this terminal open. Press Ctrl+C to stop; "
        "rerun the same command to reopen.",
        flush=True,
    )
    opened = False
    if open_browser:
        try:
            opened = webbrowser.open(f"{url}#launch={token}")
        except (webbrowser.Error, OSError):
            pass
    if not opened:
        print(f"One-time launch code: {token}", flush=True)
        print(
            "Open the address above and paste this code (valid for five minutes).",
            flush=True,
        )


class _ReadyServer(uvicorn.Server):
    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(config)
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.on_started: Callable[[], None] | None = None

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        if self.started:
            self.ready.set()
            if self.on_started is not None:
                self.on_started()


class LocalApplicationServer:
    """Own one loopback listener and the lifetime of its ASGI application."""

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        port: int | None = None,
    ) -> None:
        self.context = context
        self.port = port
        self.url = ""
        self.launch_url = ""
        self._listener_context = None
        self._listener: socket.socket | None = None
        self._server: _ReadyServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> LocalApplicationServer:
        from app.local_app import create_local_app

        chosen_port = self.context.settings.port if self.port is None else self.port
        self._listener_context = bind_loopback(
            chosen_port, allow_fallback=self.port is None
        )
        self._listener = self._listener_context.__enter__()
        try:
            application = create_local_app(self.context)
        except BaseException:
            self._listener_context.__exit__(*sys.exc_info())
            self._listener_context = None
            self._listener = None
            raise
        actual_port = self._listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{actual_port}/"
        self.launch_url = f"{self.url}#launch={application.state.launch_token}"
        self._server = _ReadyServer(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=actual_port,
                workers=1,
                log_level="warning",
                access_log=False,
            )
        )
        return self

    def run(self, *, on_started: Callable[[], None] | None = None) -> bool:
        if self._server is None or self._listener is None:
            raise RuntimeError("The local application server is not open.")
        self._server.on_started = on_started
        try:
            self._server.run(sockets=[self._listener])
        finally:
            self._server.finished.set()
        return self._server.started

    def start_background(self, *, timeout: float = 15) -> None:
        if self._server is None:
            raise RuntimeError("The local application server is not open.")
        if self._thread is not None:
            raise RuntimeError("The local application server has already started.")
        self._thread = threading.Thread(
            target=self.run,
            name="nyc-housing-local-server",
            daemon=False,
        )
        self._thread.start()
        if not self._server.ready.wait(timeout):
            self.stop()
            raise LocalSettingsError("The local application server did not start.")

    def stop(self, *, timeout: float = 15) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
            if self._thread.is_alive() and self._server is not None:
                self._server.force_exit = True
                self._thread.join(5)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()
        if self._listener_context is not None:
            self._listener_context.__exit__(exc_type, exc_value, traceback)
        self._listener_context = None
        self._listener = None


def serve_workspace(
    context: WorkspaceContext,
    *,
    port: int | None = None,
    no_browser: bool = False,
) -> int:
    with LocalApplicationServer(context, port=port) as runtime:
        started = runtime.run(
            on_started=lambda: _open_browser_or_print_code(
                runtime.url,
                runtime.launch_url.rsplit("#launch=", 1)[1],
                open_browser=context.settings.open_browser and not no_browser,
            )
        )
        return 0 if started else 3
