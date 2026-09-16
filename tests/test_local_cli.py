import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.cli.main import (
    EXIT_BUDGET_DENIED,
    EXIT_INTERRUPTED,
    EXIT_INVALID_CONFIGURATION,
    EXIT_UNAVAILABLE_DEPENDENCY,
    EXIT_VALIDATION_FAILED,
    build_parser,
    main,
)
from app.corpus.service import CorpusService, SourceArtifact
from app.jobs.runtime import OperationCancelled
from app.local_app import create_local_app
from app.storage.database import LocalStorage
from app.storage.schema import corpus_schema_metadata
from app.usage.ledger import SpendDenied
from app.workspace.context import WorkspaceContext


def test_setup_and_status_are_idempotent_and_do_not_run_paid_work(
    tmp_path, capsys
) -> None:
    root = tmp_path / "portable workspace"

    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "initialized"
    assert first["application_version"].startswith("0.1.0")
    assert "No paid request" in first["message"]
    assert first["schema_versions"] == {"corpus": 1, "state": 1}
    assert (root / "corpus.sqlite3").is_file()
    assert (root / "state.sqlite3").is_file()

    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["workspace_id"] == first["workspace_id"]
    assert second["legal_corpus"]["readiness"] == "not_installed"


def test_interactive_setup_offers_free_core_install_and_honors_decline(
    tmp_path, capsys, monkeypatch
) -> None:
    root = tmp_path / "interactive workspace"
    monkeypatch.setattr("app.cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert main(["--data-dir", str(root), "setup"]) == 0

    output = capsys.readouterr().out
    assert "five official public legal/guidance sources" in output
    assert "paid model request" in output
    assert "'accepted': False" in output
    assert "Model-backed answers and semantic search are optional" in output
    assert not (root / "artifacts" / "sources").exists()


def test_status_does_not_initialize_workspace(tmp_path, capsys) -> None:
    root = tmp_path / "absent"

    assert main(["--data-dir", str(root), "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_initialized"
    assert payload["application_version"].startswith("0.1.0")
    assert not root.exists()


def test_migration_preflight_is_read_only_and_fails_closed_without_a_path(
    tmp_path, capsys
) -> None:
    root = tmp_path / "preflight workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()

    assert main(["--data-dir", str(root), "migrate", "preflight", "--json"]) == 0
    compatible = json.loads(capsys.readouterr().out)
    assert compatible["status"] == "compatible"
    assert compatible["read_only"] is True
    assert compatible["migration_required"] is False

    storage = LocalStorage.open(
        WorkspaceContext.from_options(root, environment={}).paths
    )
    with storage.corpus_engine.begin() as connection:
        connection.execute(
            update(corpus_schema_metadata)
            .where(corpus_schema_metadata.c.key == "version")
            .values(value="2")
        )
    storage.close()

    assert (
        main(["--data-dir", str(root), "migrate", "preflight", "--json"])
        == EXIT_INVALID_CONFIGURATION
    )
    incompatible = json.loads(capsys.readouterr().out)
    assert incompatible["status"] == "migration_unavailable"
    assert incompatible["backup_required_before_migration"] is True
    assert incompatible["migration_available"] is False


def test_setup_can_prompt_for_a_workspace_credential_without_echoing_it(
    tmp_path, capsys, monkeypatch
) -> None:
    root = tmp_path / "keyed workspace"
    credential = "sk-setup-fixture-secret"
    monkeypatch.setattr("app.cli.main.getpass.getpass", lambda _prompt: credential)

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "setup",
                "--configure-openai",
                "--credential-storage",
                "file",
                "--max-paid-concurrency",
                "1",
                "--answer-deadline-seconds",
                "75",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["actions"]["credential"] == {
        "provider": "openai",
        "present": True,
        "source": "secret_file",
    }
    assert payload["usage"]["max_concurrent_paid_requests"] == 1
    saved = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert saved["answer_deadline_seconds"] == 75
    assert saved["answer_profile"] == "openai-answer-luna-v1"
    assert saved["embedding_profile"] == "openai-embedding-3-small-v1"
    assert credential not in output
    assert credential in (root / "credentials.json").read_text(encoding="utf-8")


def test_interactive_setup_offers_file_or_environment_credential_setup(
    tmp_path, capsys, monkeypatch
) -> None:
    root = tmp_path / "interactive keyed workspace"
    credential = "sk-interactive-setup-fixture"
    replies = iter(["n", "y", "y"])
    monkeypatch.setattr("app.cli.main.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(replies))
    monkeypatch.setattr(
        "app.credentials.store.KeyringCredentialStore.available", lambda _self: False
    )
    monkeypatch.setattr("app.cli.main.getpass.getpass", lambda _prompt: credential)

    assert main(["--data-dir", str(root), "setup"]) == 0

    output = capsys.readouterr().out
    saved = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    assert "No OS credential-store backend is available" in output
    assert "'source': 'secret_file'" in output
    assert saved["answer_profile"] == "openai-answer-luna-v1"
    assert saved["embedding_profile"] == "openai-embedding-3-small-v1"
    assert credential not in output
    assert credential in (root / "credentials.json").read_text(encoding="utf-8")


def test_serve_requires_initialized_workspace(tmp_path, capsys) -> None:
    result = main(["--data-dir", str(tmp_path / "absent"), "serve"])

    assert result == EXIT_INVALID_CONFIGURATION
    assert "not initialized" in capsys.readouterr().err


def test_local_app_labels_synthetic_content(tmp_path) -> None:
    context = WorkspaceContext.from_options(
        tmp_path / "data", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    storage.close()
    app = create_local_app(context)
    client = TestClient(app, base_url="http://127.0.0.1")
    exchanged = client.post(
        "/api/v1/session/exchange",
        headers={"Origin": "http://127.0.0.1"},
        json={"launch_token": app.state.launch_token},
    )

    status = client.get("/api/v1/status")
    home = client.get("/")
    result = client.post("/api/v1/demo/search", headers={"Origin": "http://127.0.0.1"})

    assert exchanged.status_code == 200
    assert status.json()["capabilities"]["legal_corpus"] is False
    assert 'id="setup-guidance"' in home.text
    assert 'id="operation-budget"' in home.text
    assert 'id="workspace-summary"' in home.text
    assert result.json()["status"] == "synthetic_demo"
    assert "not legal authority" in result.json()["warning"].lower()


def test_doctor_is_read_only_and_reports_fts5(tmp_path, capsys) -> None:
    root = tmp_path / "data"
    assert main(["--data-dir", str(root), "doctor", "--json"]) in {0, 3}

    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["checks"]["application_version"]["ok"] is True
    assert payload["checks"]["sqlite_fts5"]["ok"] is True
    assert not root.exists()


def test_feature_spec_command_aliases_parse() -> None:
    parser = build_parser()

    setup = parser.parse_args(
        ["setup", "--configure-openai", "--credential-storage", "file"]
    )
    corpus = parser.parse_args(["corpus", "import", "bundle.zip"])
    migration = parser.parse_args(
        ["migrate", "export-legacy", "bundle.zip", "--artifact-root", "artifacts"]
    )
    evaluation = parser.parse_args(["evaluate", "--offline"])
    answer_evaluation = parser.parse_args(["evaluate", "--answers", "--estimate-only"])
    retention = parser.parse_args(["maintenance", "prune", "--apply"])
    diagnostics = parser.parse_args(["diagnostics", "export", "report.json"])
    debug = parser.parse_args(["debug", "answer", "--question", "What is RPAPL 711?"])

    assert setup.configure_openai is True
    assert setup.credential_storage == "file"
    assert setup.skip_core is False
    assert corpus.corpus_command == "import"
    assert migration.migrate_command == "export-legacy"
    assert evaluation.command == "evaluate"
    assert evaluation.offline is True
    assert answer_evaluation.answers is True
    assert answer_evaluation.estimate_only is True
    assert retention.maintenance_command == "prune"
    assert retention.apply is True
    assert diagnostics.diagnostics_command == "export"
    assert debug.debug_command == "answer"
    assert debug.question == "What is RPAPL 711?"


def test_debug_answer_reports_profiles_evidence_and_usage_without_secrets(
    tmp_path, capsys
) -> None:
    root = tmp_path / "debug workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()
    context = WorkspaceContext.from_options(root, environment={})
    storage = LocalStorage.open(context.paths)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings.",
                source_url=("https://legislation.nysenate.gov/pdf/laws/RPA?full=true"),
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    storage.close()

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "debug",
                "answer",
                "--question",
                "What does RPAPL 711 cover?",
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "debug_answer"
    assert result["profiles"]["answer"]["id"] == "fake-answer-small"
    assert result["result"]["evidence"][0]["citation"] == "RPAPL \u00a7 711"
    assert result["usage"]["scope"] == "this local installation only"
    assert "credential" not in json.dumps(result["credential_presence"]).lower()


def test_manual_official_artifact_import_recovers_source_without_network(
    tmp_path, capsys
) -> None:
    root = tmp_path / "manual workspace"
    artifact = tmp_path / "official rpapl.txt"
    artifact.write_text(
        "\u00a7 711. Manually downloaded official source text.",
        encoding="utf-8",
    )
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "--offline",
                "corpus",
                "import-artifact",
                "ny-rpapl",
                str(artifact),
                "--allow-partial",
                "--json",
            ]
        )
        == 0
    )
    imported = json.loads(capsys.readouterr().out)
    assert imported["status"] == "imported"
    assert imported["source"] == "ny-rpapl"

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "--offline",
                "search",
                "manually downloaded",
                "--json",
            ]
        )
        == 0
    )
    search = json.loads(capsys.readouterr().out)
    assert search["results"][0]["citation"] == "RPAPL \u00a7 711"


def test_retention_cli_defaults_to_preview(tmp_path, capsys) -> None:
    root = tmp_path / "workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()

    assert main(["--data-dir", str(root), "maintenance", "prune", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["applied"] is False


def test_diagnostic_export_is_path_redacted_secret_free_and_no_network(
    tmp_path, capsys
) -> None:
    root = tmp_path / "private workspace"
    destination = tmp_path / "support-report.json"
    credential = "sk-diagnostic-fixture-secret"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()
    (root / "credentials.json").write_text(
        json.dumps({"openai": credential}), encoding="utf-8"
    )
    (root / "credentials.json").chmod(0o600)

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "diagnostics",
                "export",
                str(destination),
                "--json",
            ]
        )
        == 0
    )

    summary = json.loads(capsys.readouterr().out)
    report_text = destination.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert summary["network_checks_performed"] is False
    assert summary["paid_checks_performed"] is False
    assert report["status"]["data_dir"] == "<workspace>"
    assert credential not in report_text
    assert str(root) not in report_text
    assert str(tmp_path) not in report_text
    assert report["doctor"]["online_checks_requested"] is False

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "diagnostics",
                "export",
                str(destination),
                "--json",
            ]
        )
        == EXIT_UNAVAILABLE_DEPENDENCY
    )
    assert "already exists" in capsys.readouterr().err


def test_answer_evaluation_estimate_is_read_only_and_includes_review_scope(
    tmp_path, capsys
) -> None:
    root = tmp_path / "evaluation workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "evaluate",
                "--answers",
                "--estimate-only",
                "--json",
            ]
        )
        == 0
    )
    estimate = json.loads(capsys.readouterr().out)
    assert estimate["legal_case_count"] == 26
    assert estimate["property_case_count"] == 2
    assert estimate["conservative_max_cost_usd"] == "0E-8"
    assert estimate["review_status"] == "domain_review_required"


def test_cli_uses_stable_failure_categories(tmp_path, capsys, monkeypatch) -> None:
    root = tmp_path / "workspace"
    assert main(["--data-dir", str(root), "setup", "--json"]) == 0
    capsys.readouterr()

    assert (
        main(["--data-dir", str(root), "corpus", "verify", "--json"])
        == EXIT_VALIDATION_FAILED
    )
    assert "Validation failed:" in capsys.readouterr().err

    def budget_denied(_context, _args):
        raise SpendDenied("fixture budget")

    monkeypatch.setattr("app.cli.main._ask", budget_denied)
    assert main(["--data-dir", str(root), "ask", "fixture"]) == EXIT_BUDGET_DENIED
    assert "Budget denied:" in capsys.readouterr().err

    def interrupted(_context, _args):
        raise OperationCancelled("fixture cancellation")

    monkeypatch.setattr("app.cli.main._ask", interrupted)
    assert main(["--data-dir", str(root), "ask", "fixture"]) == EXIT_INTERRUPTED
    assert "Operation interrupted:" in capsys.readouterr().err

    assert (
        main(
            [
                "--data-dir",
                str(root),
                "--offline",
                "update-check",
                "--repository",
                "https://github.com/example/project",
            ]
        )
        == EXIT_UNAVAILABLE_DEPENDENCY
    )
    assert "Operation failed:" in capsys.readouterr().err
