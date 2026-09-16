import json

import pytest

from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied, NetworkPolicy
from app.workspace.paths import resolve_workspace_paths
from app.workspace.settings import LocalSettingsError, load_local_settings


def test_explicit_workspace_resolution_has_no_creation_side_effect(tmp_path) -> None:
    root = tmp_path / "portable workspace"
    paths = resolve_workspace_paths(data_dir=root, environment={})

    assert paths.root == root.resolve()
    assert paths.config_file == root.resolve() / "settings.json"
    assert not root.exists()


def test_workspace_initialization_is_explicit_and_idempotent(tmp_path) -> None:
    root = tmp_path / "housing data"
    first = WorkspaceContext.from_options(root, environment={}, initialize=True)
    second = WorkspaceContext.from_options(root, environment={}, initialize=True)

    assert first.initialized
    assert first.settings.workspace_id == second.settings.workspace_id
    assert first.paths.artifacts.is_dir()
    payload = json.loads(first.paths.config_file.read_text())
    assert payload["workspace_id"] == first.settings.workspace_id
    assert payload["max_concurrent_paid_requests"] == 2
    assert payload["answer_deadline_seconds"] == 60
    assert not any("key" in name or "secret" in name for name in payload)


def test_local_workspace_ignores_legacy_environment_and_dotenv(
    tmp_path, monkeypatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".env").write_text(
        "DATABASE_URL=postgresql+psycopg://dotenv.invalid/db\n"
        "ANSWER_LLM_API_KEY=dotenv-secret\n"
    )
    monkeypatch.chdir(checkout)
    environment = {
        "DATABASE_URL": "postgresql+psycopg://shell.invalid/db",
        "ARTIFACT_S3_BUCKET": "legacy-bucket",
        "ANSWER_LLM_API_KEY": "shell-secret",
    }

    context = WorkspaceContext.from_options(
        tmp_path / "local-data", environment=environment
    )

    assert context.paths.corpus_database.name == "corpus.sqlite3"
    assert context.settings.answer_profile == "fake-answer-small"
    assert context.detected_legacy_environment == (
        "ANSWER_LLM_API_KEY",
        "ARTIFACT_S3_BUCKET",
        "DATABASE_URL",
    )
    assert not context.paths.root.exists()


def test_two_workspaces_do_not_share_configuration(tmp_path) -> None:
    first = WorkspaceContext.from_options(
        tmp_path / "one", environment={}, initialize=True
    )
    second = WorkspaceContext.from_options(
        tmp_path / "two", environment={}, initialize=True
    )

    assert first.settings.workspace_id != second.settings.workspace_id
    assert first.paths.config_file != second.paths.config_file


def test_secret_like_settings_are_rejected(tmp_path) -> None:
    paths = resolve_workspace_paths(data_dir=tmp_path, environment={})
    paths.config_file.write_text(
        json.dumps({"workspace_id": "bad", "answer_api_key": "secret"})
    )

    with pytest.raises(LocalSettingsError, match="Secret-like field"):
        load_local_settings(paths, environment={})


def test_offline_policy_blocks_remote_but_can_allow_explicit_loopback() -> None:
    policy = NetworkPolicy(offline=True)
    with pytest.raises(NetworkAccessDenied, match="Offline mode"):
        policy.assert_url_allowed("https://example.com/data", purpose="source update")
    with pytest.raises(NetworkAccessDenied, match="Offline mode"):
        policy.assert_url_allowed("http://127.0.0.1:11434", purpose="local model")

    local_policy = NetworkPolicy(offline=True, allow_loopback_services=True)
    local_policy.assert_url_allowed("http://[::1]:11434", purpose="local model")
