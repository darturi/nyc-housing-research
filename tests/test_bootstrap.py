"""Exercise the actual POSIX wrapper with local stand-ins; never download in tests."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX bootstrap tests")
SCRIPT = Path(__file__).resolve().parents[1] / "start.sh"


@pytest.fixture
def bootstrap(tmp_path):
    repo = tmp_path / "Checkout space ü"
    repo.mkdir()
    shutil.copyfile(SCRIPT, repo / "start.sh")
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    log = tmp_path / "calls.jsonl"
    environment = os.environ.copy()
    environment.update(
        PATH=f"{command_dir}:/usr/bin:/bin",
        BOOTSTRAP_TEST_LOG=str(log),
        BOOTSTRAP_TEST_STATUS="0",
    )
    return repo, command_dir, log, environment


def uv_stub(destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['BOOTSTRAP_TEST_LOG'], 'a') as log:\n"
        "    log.write(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), "
        "'venv': os.environ.get('UV_PROJECT_ENVIRONMENT'), "
        "'offline': os.environ.get('UV_OFFLINE')}) + '\\n')\n"
        "if sys.argv[1:] == ['--version']: print('uv 0.11.14')\n"
        "if sys.argv[1] == 'sync': sys.exit(int(os.environ['BOOTSTRAP_TEST_STATUS']))\n"
    )
    destination.chmod(0o755)


def run(bootstrap, *arguments):
    repo, _, _, environment = bootstrap
    return subprocess.run(
        ["/bin/sh", str(repo / "start.sh"), *arguments],
        cwd=repo.parent,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_help_needs_no_runtime_or_network(bootstrap):
    repo, _, log, _ = bootstrap
    result = run(bootstrap, "--help")
    assert result.returncode == 0
    assert "--setup-only" in result.stdout
    assert not log.exists()
    assert not (repo / ".bootstrap").exists()


def test_existing_uv_forwards_paths_offline_and_credentials(bootstrap):
    repo, commands, log, _ = bootstrap
    uv_stub(commands / "uv")
    result = run(
        bootstrap, "--data-dir", "relative space ü", "--offline", "--setup-only"
    )
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    sync, launch = calls[-2:]
    assert sync["args"] == [
        "sync",
        "--project",
        str(repo),
        "--python",
        "3.12",
        "--locked",
        "--extra",
        "credentials",
        "--inexact",
    ]
    assert launch["args"][-5:] == [
        "start",
        "--data-dir",
        "relative space ü",
        "--offline",
        "--setup-only",
    ]
    assert "--no-env-file" in launch["args"]
    assert launch["cwd"] == str(repo.parent)
    assert launch["venv"] == str(repo / ".venv")
    assert launch["offline"] == "1"


def test_dependency_failure_never_starts_app(bootstrap):
    _, commands, log, environment = bootstrap
    uv_stub(commands / "uv")
    environment["BOOTSTRAP_TEST_STATUS"] = "7"
    result = run(bootstrap, "--setup-only")
    assert result.returncode == 3
    assert "Dependency setup failed" in result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(call["args"][0] == "run" for call in calls)


def test_missing_uv_downloads_private_versioned_runtime(bootstrap):
    repo, commands, log, environment = bootstrap
    source_uv = commands / "fixture-uv"
    uv_stub(source_uv)
    # The fake HTTPS download writes a tiny installer using the same destination
    # contract as Astral. No network or shell-profile changes are possible.
    environment["BOOTSTRAP_TEST_UV"] = str(source_uv)
    curl = commands / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys\n"
        "assert 'https://astral.sh/uv/0.11.14/install.sh' in sys.argv\n"
        "target = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "target.write_text('mkdir -p \"$UV_UNMANAGED_INSTALL\"\\n' "
        '+ \'cp "$BOOTSTRAP_TEST_UV" "$UV_UNMANAGED_INSTALL/uv"\\n\')\n'
    )
    curl.chmod(0o755)
    result = run(bootstrap, "--skip-core", "--setup-only")
    assert result.returncode == 0, result.stderr
    assert (repo / ".bootstrap/uv/0.11.14/uv").is_file()
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert [call["args"][0] for call in calls] == ["sync", "run"]


def test_missing_uv_offline_fails_without_download(bootstrap):
    repo, _, log, _ = bootstrap
    result = run(bootstrap, "--offline")
    assert result.returncode == 3
    assert "runtime is missing" in result.stderr
    assert not log.exists()
    assert not (repo / ".bootstrap").exists()
