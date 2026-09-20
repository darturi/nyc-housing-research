import json
import os
import signal
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import update

from app.cli.main import main
from app.corpus.manifests import load_core_manifests
from app.corpus.service import CorpusService, SourceArtifact
from app.jobs.service import JobService, JobState
from app.launcher import (
    LocalApplicationServer,
    _open_browser_or_print_code,
    bind_loopback,
    prepare_sources,
    prepare_workspace,
    workspace_launch_lock,
)
from app.storage.database import LocalStorage, SchemaVersionError
from app.storage.schema import state_schema_metadata
from app.workspace.context import WorkspaceContext


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.delenv("NYC_HOUSING_CONFIG", raising=False)
    monkeypatch.delenv("NYC_HOUSING_OFFLINE", raising=False)
    return WorkspaceContext.from_options(tmp_path / "Space ü workspace", environment={})


def artifact():
    return SourceArtifact(
        slug="ny-rpapl",
        content=b"\xc2\xa7 711. Grounds for summary proceedings.",
        source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
        content_type="text/plain",
        retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
    )


def test_start_initializes_without_prompts_or_paid_work(context, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Setup must not prompt or call a model provider")

    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr("getpass.getpass", forbidden)
    monkeypatch.setattr("app.providers.gateway.ProviderGateway.__init__", forbidden)
    assert (
        main(
            [
                "start",
                "--data-dir",
                str(context.paths.root),
                "--skip-core",
                "--setup-only",
            ]
        )
        == 0
    )
    original = context.paths.config_file.read_bytes()
    assert (
        main(
            [
                "--data-dir",
                str(context.paths.root),
                "start",
                "--skip-core",
                "--setup-only",
            ]
        )
        == 0
    )
    assert context.paths.config_file.read_bytes() == original
    with context.paths.corpus_database.open("rb") as database:
        assert database.read(6) == b"SQLite"


def test_offline_start_never_downloads_or_persists_one_time_flag(context, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline setup must not download")

    monkeypatch.setattr("app.jobs.maintenance.download_source_artifacts", forbidden)
    assert (
        main(
            [
                "start",
                "--data-dir",
                str(context.paths.root),
                "--offline",
                "--setup-only",
            ]
        )
        == 0
    )
    assert json.loads(context.paths.config_file.read_text())["offline"] is False


def test_repeated_start_preserves_partial_sources_settings_and_credentials(
    context, monkeypatch
):
    storage = prepare_workspace(context)
    generation = CorpusService(storage).install_artifacts(
        [artifact()], allow_partial=True
    )
    storage.close()
    settings = json.loads(context.paths.config_file.read_text())
    settings.update(monthly_budget_usd="7.25", answer_profile="openai-answer-luna-v1")
    context.paths.config_file.write_text(json.dumps(settings))
    secret = context.paths.root / "credentials.json"
    secret.write_text('{"openai": "fixture-do-not-read"}')
    before = (context.paths.config_file.read_bytes(), secret.read_bytes())

    def forbidden(*_args, **_kwargs):
        pytest.fail("Existing source libraries must not be reinstalled")

    monkeypatch.setattr("app.jobs.maintenance.download_source_artifacts", forbidden)
    assert main(["start", "--data-dir", str(context.paths.root), "--setup-only"]) == 0
    assert (context.paths.config_file.read_bytes(), secret.read_bytes()) == before
    storage = LocalStorage.open(context.paths)
    assert CorpusService(storage).status().active_generation_id == generation
    storage.close()


def test_incompatible_schema_is_rejected_before_settings_are_written(context):
    storage = prepare_workspace(context)
    with storage.state_engine.begin() as connection:
        connection.execute(update(state_schema_metadata).values(value="99"))
    storage.close()
    original = context.paths.config_file.read_bytes()
    with pytest.raises(SchemaVersionError, match="migrate preflight"):
        prepare_workspace(context)
    assert context.paths.config_file.read_bytes() == original
    storage = LocalStorage.open(context.paths)
    assert storage.versions()["state"] == 99
    storage.close()


def test_missing_database_is_not_silently_recreated(context):
    storage = prepare_workspace(context)
    storage.close()
    context.paths.state_database.unlink()
    with pytest.raises(SchemaVersionError, match="incomplete"):
        prepare_workspace(context)
    assert not context.paths.state_database.exists()


def test_failed_download_retries_same_free_job_and_then_verifies(context, monkeypatch):
    calls = []

    def download(_context, slugs, *, progress):
        calls.append(slugs)
        progress("Downloading source 1/5: ny-rpapl")
        if len(calls) == 1:
            raise OSError("Publisher is temporarily unavailable")
        return [artifact()]

    monkeypatch.setattr("app.jobs.maintenance.download_source_artifacts", download)
    # This test exercises real durable jobs, with a one-source test core.
    manifest = load_core_manifests()["ny-rpapl"]
    monkeypatch.setattr(
        "app.corpus.service.load_core_manifests", lambda: {"ny-rpapl": manifest}
    )
    storage = prepare_workspace(context)
    try:
        assert prepare_sources(context, storage) is False
        failed = JobService(storage.state_engine).list()[0]
        assert failed.state == JobState.FAILED
        assert prepare_sources(context, storage) is True
        jobs = JobService(storage.state_engine).list()
        assert len(jobs) == 1
        assert jobs[0].id == failed.id
        assert jobs[0].state == JobState.SUCCEEDED
        assert CorpusService(storage).verify()["chunk_count"] > 0
    finally:
        storage.close()


def test_source_failure_opens_recovery_ui_but_setup_only_fails(context, monkeypatch):
    monkeypatch.setattr("app.launcher.prepare_sources", lambda *_: False)
    launched = []
    monkeypatch.setattr(
        "app.launcher.serve_workspace",
        lambda *args, **kwargs: launched.append(kwargs) or 0,
    )
    args = ["start", "--data-dir", str(context.paths.root)]
    assert main([*args, "--setup-only"]) == 3
    assert not launched
    assert main([*args, "--no-browser", "--port", "8123"]) == 0
    assert launched == [{"port": 8123, "no_browser": True}]


def test_start_never_resumes_paid_work(context, monkeypatch):
    storage = prepare_workspace(context)
    jobs = JobService(storage.state_engine)
    paid = jobs.create("corpus_index", "corpus:core", resume={"operation": "index"})
    jobs.claim(paid.id, "old-worker", lease_seconds=-1)
    try:
        assert prepare_sources(context, storage) is False
        assert jobs.get(paid.id).state == JobState.PAUSED
    finally:
        storage.close()


def test_duplicate_launcher_rejected_and_lock_reusable(context):
    with workspace_launch_lock(context.paths.root):
        process = subprocess.run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "from app.launcher import workspace_launch_lock; "
                "\nwith workspace_launch_lock(Path(__import__('sys').argv[1])): pass",
                str(context.paths.root),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert process.returncode != 0
        assert "already starting or running" in process.stderr
    with workspace_launch_lock(context.paths.root):
        pass


def test_occupied_default_port_falls_back_but_explicit_port_fails():
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        with bind_loopback(port, allow_fallback=True) as listener:
            assert listener.getsockname()[0] == "127.0.0.1"
            assert listener.getsockname()[1] != port
        with pytest.raises(ValueError, match="--port"):
            with bind_loopback(port, allow_fallback=False):
                pytest.fail("Explicit occupied port should fail")


def test_background_server_serves_and_stops(context):
    storage = prepare_workspace(context)
    storage.close()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with LocalApplicationServer(context, port=port) as runtime:
        runtime.start_background()
        response = httpx.get(runtime.url, trust_env=False)
        assert response.status_code == 200
        assert runtime.launch_url.startswith(runtime.url + "#launch=")


@pytest.mark.parametrize("failure", [False, OSError("No desktop")])
def test_browser_failure_prints_usable_code(monkeypatch, capsys, failure):
    def browser(_url):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr("app.launcher.webbrowser.open", browser)
    _open_browser_or_print_code(
        "http://127.0.0.1:8000/", "fixture-code", open_browser=True
    )
    output = capsys.readouterr().out
    assert "One-time launch code: fixture-code" in output
    assert "five minutes" in output


def test_live_start_serves_authenticated_ui_and_stops(context, tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    output_path = tmp_path / "launcher-output.txt"
    with output_path.open("w+") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.cli.main",
                "start",
                "--data-dir",
                str(context.paths.root),
                "--skip-core",
                "--no-browser",
                "--port",
                str(port),
            ],
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
        )
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                output.seek(0)
                text = output.read()
                if "One-time launch code: " in text:
                    break
                assert process.poll() is None, text
                time.sleep(0.1)
            else:
                pytest.fail("Launcher did not report readiness: " + text)
            token = text.split("One-time launch code: ", 1)[1].splitlines()[0]
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=base, trust_env=False) as client:
                assert client.get("/").status_code == 200
                exchanged = client.post(
                    "/api/v1/session/exchange",
                    headers={"Origin": base},
                    json={"launch_token": token},
                )
                assert exchanged.status_code == 200
                assert client.get("/api/v1/status").json()["status"] == "initialized"
        finally:
            if os.name != "nt":
                process.send_signal(signal.SIGINT)
            else:
                process.terminate()
            process.wait(timeout=15)
    if os.name != "nt":
        assert process.returncode == 5
