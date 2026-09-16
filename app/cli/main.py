from __future__ import annotations

import argparse
import getpass
import json
import mimetypes
import os
import sys
import uuid
import webbrowser
from collections.abc import Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from app import __version__
from app.answer.evaluation import (
    answer_evaluation_payload,
    estimate_answer_evaluation,
    run_answer_evaluation,
)
from app.answer.local import LocalAnswerService
from app.corpus.bundle import CanonicalBundleService
from app.corpus.download import SourceDownloadError
from app.corpus.service import CorpusService, CorpusValidationError, SourceArtifact
from app.credentials.store import (
    CredentialResolver,
    CredentialStoreError,
    configured_credential_slots,
    validate_credential,
)
from app.exporting.service import ResearchExporter
from app.hpd.cache import CachedPropertyRepository, PropertyCacheError
from app.hpd.complete import export_complete_property_result
from app.hpd.connector import (
    HpdSocrataConnector,
    PropertyConnectorError,
    PropertyQuery,
)
from app.jobs.maintenance import CorpusMaintenanceJobs
from app.jobs.mutations import run_corpus_mutation
from app.jobs.property_exports import PropertyExportJobs
from app.jobs.runtime import Deadline, OperationCancelled, OperationDeadlineExceeded
from app.jobs.service import (
    InvalidJobTransition,
    JobConflict,
    JobNotFound,
    JobService,
)
from app.legacy.exporter import (
    LegacyCorpusExporter,
    LegacyExportError,
    database_url_from_environment,
)
from app.maintenance.backup import BackupError, WorkspaceBackupService, restore_backup
from app.maintenance.diagnostics import (
    doctor_checks,
    export_redacted_diagnostics,
    workspace_status,
)
from app.maintenance.retention import RetentionError, WorkspaceRetentionService
from app.maintenance.updates import UpdateCheckError, check_github_release
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    configured_profile_entries,
    get_configured_profile,
    get_profile,
)
from app.providers.validation import (
    profile_compatibility_estimate,
    run_credential_validation,
    run_profile_compatibility_check,
)
from app.retrieval.benchmark import benchmark_as_dict, run_synthetic_benchmark
from app.retrieval.evaluation import evaluate_retrieval, load_retrieval_cases
from app.retrieval.local import LocalSearch, LocalSearchFilters
from app.storage.database import LocalStorage, SchemaVersionError
from app.storage.schema import CORPUS_SCHEMA_VERSION, STATE_SCHEMA_VERSION
from app.usage.ledger import PaidCapacityUnavailable, SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.network import NetworkAccessDenied, NetworkPolicy
from app.workspace.settings import LocalSettingsError, save_local_settings

EXIT_INVALID_CONFIGURATION = 2
EXIT_UNAVAILABLE_DEPENDENCY = 3
EXIT_BUDGET_DENIED = 4
EXIT_INTERRUPTED = 5
EXIT_VALIDATION_FAILED = 6


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyc-housing",
        description="Operate a local NYC Housing research workspace.",
    )
    parser.add_argument("--data-dir", help="Use an explicit local data directory.")
    parser.add_argument("--config", help="Use an explicit local settings file.")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Block application-managed remote HTTP access.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    setup = commands.add_parser("setup", help="Initialize a local workspace.")
    setup.add_argument("--port", type=int, help="Set the default loopback port.")
    setup.add_argument("--monthly-budget", help="Set the monthly API budget in USD.")
    setup.add_argument(
        "--per-operation-budget",
        help="Set the per-operation API budget in USD.",
    )
    setup.add_argument(
        "--max-paid-concurrency",
        type=int,
        choices=[1, 2],
        help="Limit concurrent paid provider calls to one or two.",
    )
    setup.add_argument("--answer-deadline-seconds", type=int)
    setup.add_argument("--answer-profile")
    setup.add_argument("--embedding-profile")
    setup.add_argument("--property-cache-retention-days", type=int)
    setup.add_argument("--operational-retention-days", type=int)
    setup.add_argument("--usage-retention-months", type=int)
    setup.add_argument(
        "--configure-openai",
        action="store_true",
        help="Prompt without echo and store an OpenAI credential.",
    )
    setup.add_argument(
        "--credential-storage",
        choices=["keyring", "file"],
        default="keyring",
        help="Credential storage used with --configure-openai.",
    )
    setup_core = setup.add_mutually_exclusive_group()
    setup_core.add_argument(
        "--install-core",
        action="store_true",
        help="Download, validate, and activate the free text-only core corpus.",
    )
    setup_core.add_argument(
        "--skip-core",
        action="store_true",
        help="Initialize only; do not offer or start the core source download.",
    )
    setup.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser when serving by default.",
    )
    setup.add_argument("--json", action="store_true")

    status = commands.add_parser("status", help="Show local workspace readiness.")
    status.add_argument("--json", action="store_true")

    doctor = commands.add_parser("doctor", help="Run read-only local diagnostics.")
    doctor.add_argument(
        "--online",
        action="store_true",
        help="Also run a bounded, non-paid HPD public API check.",
    )
    doctor.add_argument("--json", action="store_true")

    diagnostics = commands.add_parser(
        "diagnostics", help="Export a path-redacted local diagnostic report."
    )
    diagnostics_commands = diagnostics.add_subparsers(
        dest="diagnostics_command", required=True
    )
    diagnostics_export = diagnostics_commands.add_parser(
        "export", help="Write a non-network, non-paid diagnostic JSON file."
    )
    diagnostics_export.add_argument("path")
    diagnostics_export.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace an existing diagnostic file.",
    )
    diagnostics_export.add_argument("--json", action="store_true")

    serve = commands.add_parser("serve", help="Start the loopback browser app.")
    serve.add_argument("--port", type=int, help="Override the configured port.")
    serve.add_argument("--no-browser", action="store_true")

    sources = commands.add_parser("sources", help="Show core source availability.")
    sources.add_argument("--json", action="store_true")

    corpus = commands.add_parser("corpus", help="Manage the local legal corpus.")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    install = corpus_commands.add_parser("install", help="Install legal sources.")
    install.add_argument("collection", choices=["core"])
    install.add_argument("--text-only", action="store_true")
    install.add_argument("--json", action="store_true")
    update_command = corpus_commands.add_parser(
        "update", help="Download and stage current legal sources."
    )
    update_command.add_argument("--source")
    update_command.add_argument(
        "--allow-partial",
        action="store_true",
        help="Explicitly permit activation while core modules are missing.",
    )
    update_command.add_argument("--json", action="store_true")
    verify = corpus_commands.add_parser("verify", help="Verify corpus artifacts.")
    verify.add_argument("--generation")
    verify.add_argument("--json", action="store_true")
    activate = corpus_commands.add_parser("activate", help="Activate a generation.")
    activate.add_argument("generation_id")
    activate.add_argument("--allow-partial", action="store_true")
    activate.add_argument("--json", action="store_true")
    rollback = corpus_commands.add_parser("rollback", help="Restore prior generation.")
    rollback.add_argument("--json", action="store_true")

    bundle = corpus_commands.add_parser(
        "bundle", help="Export, inspect, or import a data-only corpus bundle."
    )
    bundle_commands = bundle.add_subparsers(dest="bundle_command", required=True)
    bundle_export = bundle_commands.add_parser("export")
    bundle_export.add_argument("path")
    bundle_export.add_argument("--generation")
    bundle_export.add_argument("--json", action="store_true")
    bundle_inspect = bundle_commands.add_parser("inspect")
    bundle_inspect.add_argument("path")
    bundle_inspect.add_argument("--json", action="store_true")
    bundle_import = bundle_commands.add_parser("import")
    bundle_import.add_argument("path")
    bundle_import.add_argument("--allow-partial", action="store_true")
    bundle_import.add_argument("--no-activate", action="store_true")
    bundle_import.add_argument("--json", action="store_true")
    corpus_import = corpus_commands.add_parser(
        "import",
        help="Validate and import a canonical data-only corpus bundle.",
    )
    corpus_import.add_argument("path")
    corpus_import.add_argument("--allow-partial", action="store_true")
    corpus_import.add_argument("--no-activate", action="store_true")
    corpus_import.add_argument("--json", action="store_true")
    artifact_import = corpus_commands.add_parser(
        "import-artifact",
        help="Validate a manually downloaded official source artifact.",
    )
    artifact_import.add_argument("source")
    artifact_import.add_argument("path")
    artifact_import.add_argument("--source-url")
    artifact_import.add_argument("--content-type")
    artifact_import.add_argument("--allow-partial", action="store_true")
    artifact_import.add_argument("--no-activate", action="store_true")
    artifact_import.add_argument("--json", action="store_true")
    index = corpus_commands.add_parser(
        "index", help="Build a staged embedding index for the active corpus."
    )
    index.add_argument("--approve-cost", action="store_true")
    index.add_argument("--estimate-only", action="store_true")
    index.add_argument("--max-cost-usd", type=Decimal)
    index.add_argument("--batch-size", type=int, default=32)
    index.add_argument("--json", action="store_true")

    search = commands.add_parser("search", help="Search installed legal sources.")
    search.add_argument("query")
    search.add_argument("--source")
    search.add_argument("--source-type")
    search.add_argument("--jurisdiction")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--json", action="store_true")

    jobs_command = commands.add_parser("jobs", help="Inspect or control local jobs.")
    jobs_subcommands = jobs_command.add_subparsers(dest="jobs_command", required=True)
    jobs_list = jobs_subcommands.add_parser("list")
    jobs_list.add_argument("--json", action="store_true")
    jobs_resume = jobs_subcommands.add_parser("resume")
    jobs_resume.add_argument("job_id")
    jobs_resume.add_argument("--json", action="store_true")
    jobs_cancel = jobs_subcommands.add_parser("cancel")
    jobs_cancel.add_argument("job_id")
    jobs_cancel.add_argument("--json", action="store_true")

    credentials = commands.add_parser(
        "credentials", help="Manage workspace-scoped provider credentials."
    )
    credential_commands = credentials.add_subparsers(
        dest="credentials_command", required=True
    )
    credentials_status = credential_commands.add_parser("status")
    credentials_status.add_argument("--json", action="store_true")
    credentials_set = credential_commands.add_parser("set")
    credentials_set.add_argument(
        "provider",
        help="Built-in provider or configured advanced credential slot.",
    )
    credentials_set.add_argument(
        "--storage", choices=["keyring", "file"], default="keyring"
    )
    credentials_set.add_argument("--json", action="store_true")
    credentials_delete = credential_commands.add_parser("delete")
    credentials_delete.add_argument(
        "provider",
        help="Built-in provider or configured advanced credential slot.",
    )
    credentials_delete.add_argument(
        "--storage", choices=["keyring", "file"], default="keyring"
    )
    credentials_delete.add_argument("--json", action="store_true")
    credentials_validate = credential_commands.add_parser(
        "validate",
        help="Run an explicit, minimally metered provider capability check.",
    )
    credentials_validate.add_argument("provider", choices=["openai"])
    credentials_validate.add_argument("--approve-cost", action="store_true")
    credentials_validate.add_argument("--max-cost-usd", type=Decimal)
    credentials_validate.add_argument("--json", action="store_true")

    profiles = commands.add_parser("profiles", help="List or select provider profiles.")
    profile_commands = profiles.add_subparsers(dest="profiles_command", required=True)
    profiles_list = profile_commands.add_parser("list")
    profiles_list.add_argument("--json", action="store_true")
    profiles_select = profile_commands.add_parser("select")
    profiles_select.add_argument("kind", choices=["answer", "embedding"])
    profiles_select.add_argument("profile_id")
    profiles_select.add_argument("--json", action="store_true")
    profiles_endpoint = profile_commands.add_parser(
        "configure-endpoint",
        help="Configure an advanced OpenAI-compatible endpoint for a selected profile.",
    )
    profiles_endpoint.add_argument("kind", choices=["answer", "embedding"])
    profiles_endpoint.add_argument("endpoint")
    profiles_endpoint.add_argument("--auth-slot")
    profiles_endpoint.add_argument("--input-price", type=Decimal)
    profiles_endpoint.add_argument("--output-price", type=Decimal)
    profiles_endpoint.add_argument("--price-effective-date")
    profiles_endpoint.add_argument("--price-source")
    profiles_endpoint.add_argument(
        "--pricing-unknown",
        action="store_true",
        help=(
            "Declare that pricing is unknown. Only explicitly approved one-off "
            "requests will be allowed, outside USD budget caps."
        ),
    )
    profiles_endpoint.add_argument(
        "--stores-response", choices=["yes", "no"], required=True
    )
    profiles_endpoint.add_argument("--json", action="store_true")
    profiles_clear = profile_commands.add_parser(
        "clear-endpoint", help="Remove the advanced override for a selected profile."
    )
    profiles_clear.add_argument("kind", choices=["answer", "embedding"])
    profiles_clear.add_argument("--json", action="store_true")
    profiles_check = profile_commands.add_parser(
        "check",
        help="Explicitly test the selected provider profile without fallback.",
    )
    profiles_check.add_argument("kind", choices=["answer", "embedding"])
    profiles_check.add_argument("--estimate-only", action="store_true")
    profiles_check.add_argument("--approve-cost", action="store_true")
    profiles_check.add_argument("--max-cost-usd", type=Decimal)
    profiles_check.add_argument("--allow-unknown-cost", action="store_true")
    profiles_check.add_argument("--json", action="store_true")

    usage = commands.add_parser("usage", help="Show local API spend accounting.")
    usage.add_argument("--json", action="store_true")

    ask = commands.add_parser(
        "ask", help="Generate a cited answer from local evidence."
    )
    ask.add_argument("question")
    ask.add_argument("--source")
    ask.add_argument("--limit", type=int, default=8)
    ask.add_argument("--deadline", type=float)
    ask.add_argument("--allow-unknown-cost", action="store_true")
    ask.add_argument("--json", action="store_true")

    debug = commands.add_parser(
        "debug", help="Inspect a local operation without exposing credentials."
    )
    debug_commands = debug.add_subparsers(dest="debug_command", required=True)
    debug_answer = debug_commands.add_parser(
        "answer", help="Inspect one metered answer and its evidence/profiles."
    )
    debug_answer.add_argument("--question", required=True)
    debug_answer.add_argument("--source")
    debug_answer.add_argument("--limit", type=int, default=8)
    debug_answer.add_argument("--deadline", type=float)
    debug_answer.add_argument("--allow-unknown-cost", action="store_true")
    debug_answer.add_argument("--json", action="store_true")

    property_command = commands.add_parser(
        "property", help="Query or manage the local HPD property cache."
    )
    property_subcommands = property_command.add_subparsers(
        dest="property_command", required=True
    )
    property_search = property_subcommands.add_parser(
        "search", help="Search the official NYC HPD violations dataset."
    )
    property_search.add_argument("--building-id")
    property_search.add_argument("--registration-id")
    property_search.add_argument("--house-number")
    property_search.add_argument("--street-name")
    property_search.add_argument("--borough")
    property_search.add_argument("--zip-code")
    property_search.add_argument("--violation-class")
    property_search.add_argument("--status", choices=["open", "closed"])
    property_search.add_argument("--inspection-date-from")
    property_search.add_argument("--inspection-date-to")
    property_search.add_argument("--limit", type=int, default=50)
    property_search.add_argument("--continuation")
    property_search.add_argument("--refresh", action="store_true")
    property_search.add_argument("--json", action="store_true")
    property_clear = property_subcommands.add_parser(
        "cache-clear", help="Delete cached HPD property responses."
    )
    property_clear.add_argument("--json", action="store_true")
    property_export = property_subcommands.add_parser(
        "export", help="Fetch and export bounded property-result pages."
    )
    property_export.add_argument("--building-id")
    property_export.add_argument("--registration-id")
    property_export.add_argument("--house-number")
    property_export.add_argument("--street-name")
    property_export.add_argument("--borough")
    property_export.add_argument("--zip-code")
    property_export.add_argument("--violation-class")
    property_export.add_argument("--status", choices=["open", "closed"])
    property_export.add_argument("--inspection-date-from")
    property_export.add_argument("--inspection-date-to")
    property_export.add_argument("--limit", type=int, default=100)
    property_export.add_argument("--max-pages", type=int, default=20)
    property_export.add_argument("--deadline", type=float, default=120.0)
    property_export.add_argument("--json", action="store_true")

    backup = commands.add_parser(
        "backup", help="Create a portable, secret-free workspace backup."
    )
    backup.add_argument("path")
    backup.add_argument("--include-property-cache", action="store_true")
    backup.add_argument("--json", action="store_true")

    restore = commands.add_parser(
        "restore", help="Restore a workspace backup into a new directory."
    )
    restore.add_argument("archive")
    restore.add_argument("--destination", required=True)
    restore.add_argument("--json", action="store_true")

    maintenance = commands.add_parser(
        "maintenance", help="Preview or apply safe local retention cleanup."
    )
    maintenance_commands = maintenance.add_subparsers(
        dest="maintenance_command", required=True
    )
    prune = maintenance_commands.add_parser(
        "prune", help="Preview retention candidates; pass --apply to delete them."
    )
    prune.add_argument(
        "--apply",
        action="store_true",
        help="Apply the previewed policy after checking maintenance conflicts.",
    )
    prune.add_argument("--json", action="store_true")

    legacy = commands.add_parser(
        "legacy", help="Explicitly access read-only legacy migration tools."
    )
    legacy_commands = legacy.add_subparsers(dest="legacy_command", required=True)
    legacy_export = legacy_commands.add_parser(
        "export", help="Export current legal evidence from a legacy database."
    )
    legacy_export.add_argument("path")
    legacy_export.add_argument("--artifact-root", required=True)
    legacy_export.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Environment variable containing the legacy database URL.",
    )
    legacy_export.add_argument("--json", action="store_true")

    migrate = commands.add_parser(
        "migrate", help="Preflight local schemas or export a read-only legacy corpus."
    )
    migrate_commands = migrate.add_subparsers(dest="migrate_command", required=True)
    migrate_preflight = migrate_commands.add_parser(
        "preflight",
        help="Check local schema compatibility without changing the workspace.",
    )
    migrate_preflight.add_argument("--json", action="store_true")
    migrate_export = migrate_commands.add_parser(
        "export-legacy",
        help="Export current legal evidence from a legacy database.",
    )
    migrate_export.add_argument("path")
    migrate_export.add_argument("--artifact-root", required=True)
    migrate_export.add_argument(
        "--database-url-env",
        default="DATABASE_URL",
        help="Environment variable containing the legacy database URL.",
    )
    migrate_export.add_argument("--json", action="store_true")

    retrieval_evaluation = commands.add_parser(
        "evaluate-retrieval",
        help="Run the deterministic 55-case local retrieval gate.",
    )
    retrieval_evaluation.add_argument("--case-file")
    retrieval_evaluation.add_argument("--k", type=int, default=5)
    retrieval_evaluation.add_argument("--minimum-recall", type=float, default=0.80)
    retrieval_evaluation.add_argument("--minimum-mrr", type=float, default=0.80)
    retrieval_evaluation.add_argument("--json", action="store_true")

    evaluate = commands.add_parser(
        "evaluate",
        help="Run the deterministic local retrieval gate without paid calls.",
    )
    evaluate.add_argument("--case-file")
    evaluate.add_argument("--k", type=int, default=5)
    evaluate.add_argument("--minimum-recall", type=float, default=0.80)
    evaluate.add_argument("--minimum-mrr", type=float, default=0.80)
    evaluate.add_argument(
        "--offline",
        action="store_true",
        help="Explicitly block network access during the evaluation.",
    )
    evaluate.add_argument(
        "--answers",
        action="store_true",
        help="Run the separate answer-generation review suite.",
    )
    evaluate.add_argument(
        "--estimate-only",
        action="store_true",
        help="Report the conservative answer-suite ceiling without provider calls.",
    )
    evaluate.add_argument("--approve-cost", action="store_true")
    evaluate.add_argument("--max-cost-usd", type=Decimal)
    evaluate.add_argument("--legal-case-file")
    evaluate.add_argument("--json", action="store_true")

    benchmark = commands.add_parser(
        "benchmark", help="Run the synthetic local retrieval performance gate."
    )
    benchmark.add_argument("--chunks", type=int, default=10_000)
    benchmark.add_argument("--dimension", type=int, default=1_536)
    benchmark.add_argument("--runs", type=int, default=20)
    benchmark.add_argument("--json", action="store_true")

    update_check = commands.add_parser(
        "update-check", help="Check GitHub release metadata without changing files."
    )
    update_check.add_argument(
        "--repository",
        required=True,
        help="Verified https://github.com/OWNER/REPOSITORY URL.",
    )
    update_check.add_argument("--json", action="store_true")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        context = _context(args, initialize=args.command == "setup")
        if args.command == "setup":
            return _setup(context, args)
        if args.command == "status":
            return _status(context, as_json=args.json)
        if args.command == "doctor":
            return _doctor(context, online=args.online, as_json=args.json)
        if args.command == "diagnostics":
            return _diagnostics(context, args)
        if args.command == "serve":
            return _serve(context, args)
        if args.command == "sources":
            return _sources(context, as_json=args.json)
        if args.command == "corpus":
            return _corpus(context, args)
        if args.command == "search":
            return _search(context, args)
        if args.command == "jobs":
            return _jobs(context, args)
        if args.command == "credentials":
            return _credentials(context, args)
        if args.command == "profiles":
            return _profiles(context, args)
        if args.command == "usage":
            return _usage(context, as_json=args.json)
        if args.command == "ask":
            return _ask(context, args)
        if args.command == "debug":
            return _debug_answer(context, args)
        if args.command == "property":
            return _property(context, args)
        if args.command == "backup":
            return _backup(context, args)
        if args.command == "restore":
            return _restore(args)
        if args.command == "maintenance":
            return _maintenance(context, args)
        if args.command == "migrate" and args.migrate_command == "preflight":
            return _migration_preflight(context, args)
        if args.command in {"legacy", "migrate"}:
            return _legacy(args)
        if args.command in {"evaluate", "evaluate-retrieval"}:
            return _evaluate_retrieval(context, args)
        if args.command == "benchmark":
            return _benchmark(args)
        if args.command == "update-check":
            return _update_check(context, args)
    except (LocalSettingsError, SchemaVersionError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    except SpendDenied as exc:
        print(f"Budget denied: {exc}", file=sys.stderr)
        return EXIT_BUDGET_DENIED
    except (OperationCancelled, OperationDeadlineExceeded) as exc:
        print(f"Operation interrupted: {exc}", file=sys.stderr)
        return EXIT_INTERRUPTED
    except CorpusValidationError as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return EXIT_VALIDATION_FAILED
    except (
        SourceDownloadError,
        NetworkAccessDenied,
        JobConflict,
        JobNotFound,
        InvalidJobTransition,
        CredentialStoreError,
        PaidCapacityUnavailable,
        ProviderExecutionError,
        PropertyCacheError,
        PropertyConnectorError,
        BackupError,
        RetentionError,
        LegacyExportError,
        UpdateCheckError,
        ValueError,
    ) as exc:
        print(f"Operation failed: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE_DEPENDENCY
    return EXIT_INVALID_CONFIGURATION


def _context(args: argparse.Namespace, *, initialize: bool) -> WorkspaceContext:
    context = WorkspaceContext.from_options(
        data_dir=args.data_dir,
        config_file=args.config,
        initialize=False,
    )
    settings = context.settings
    if args.offline and not settings.offline:
        settings = replace(settings, offline=True)
    if args.command == "setup":
        if args.port is not None:
            settings = replace(settings, port=args.port)
        if args.no_browser:
            settings = replace(settings, open_browser=False)
        if args.monthly_budget is not None:
            settings = replace(settings, monthly_budget_usd=args.monthly_budget)
        if args.per_operation_budget is not None:
            settings = replace(
                settings,
                per_operation_budget_usd=args.per_operation_budget,
            )
        if args.max_paid_concurrency is not None:
            settings = replace(
                settings,
                max_concurrent_paid_requests=args.max_paid_concurrency,
            )
        if args.answer_deadline_seconds is not None:
            settings = replace(
                settings,
                answer_deadline_seconds=args.answer_deadline_seconds,
            )
        if args.answer_profile is not None:
            settings = replace(settings, answer_profile=args.answer_profile)
        if args.embedding_profile is not None:
            settings = replace(settings, embedding_profile=args.embedding_profile)
        if args.property_cache_retention_days is not None:
            settings = replace(
                settings,
                property_cache_retention_days=args.property_cache_retention_days,
            )
        if args.operational_retention_days is not None:
            settings = replace(
                settings,
                operational_retention_days=args.operational_retention_days,
            )
        if args.usage_retention_months is not None:
            settings = replace(
                settings, usage_retention_months=args.usage_retention_months
            )
    context = replace(
        context,
        settings=settings.validate(),
        network=NetworkPolicy(offline=settings.offline),
    )
    if initialize:
        context.initialize()
    return context


def _setup(context: WorkspaceContext, args: argparse.Namespace) -> int:
    save_local_settings(context.paths, context.settings)
    storage = LocalStorage.open(context.paths, initialize=True)
    actions: dict[str, object] = {}
    try:
        install_core = args.install_core
        configure_openai = args.configure_openai
        credential_storage = args.credential_storage
        select_openai_profiles = configure_openai
        interactive = not args.json and sys.stdin.isatty()
        corpus_status = CorpusService(storage).status()
        if (
            not install_core
            and not args.skip_core
            and interactive
            and corpus_status.active_generation_id is None
        ):
            print(
                "Core installation downloads five official public legal/guidance "
                "sources and builds free local text search. It does not build "
                "embeddings or make a paid model request."
            )
            try:
                reply = input("Install the resumable text-only core now? [Y/n] ")
            except EOFError:
                reply = "n"
            install_core = reply.strip().lower() not in {"n", "no"}
            actions["core_install_offer"] = {
                "accepted": install_core,
                "paid_requests": False,
            }
        selected_profiles_are_synthetic = any(
            get_profile(profile_id).provider == "fake"
            for profile_id in (
                context.settings.answer_profile,
                context.settings.embedding_profile,
            )
        )
        if interactive and not configure_openai and selected_profiles_are_synthetic:
            print(
                "Model-backed answers and semantic search are optional. They use "
                "your own OpenAI credential and can incur charges only when you "
                "explicitly start a model-backed action. Adding a credential now "
                "does not contact OpenAI or make a paid request."
            )
            try:
                reply = input("Configure the packaged OpenAI profiles now? [y/N] ")
            except EOFError:
                reply = "n"
            select_openai_profiles = reply.strip().lower() in {"y", "yes"}
            actions["model_setup_offer"] = {
                "accepted": select_openai_profiles,
                "paid_requests": False,
            }
            if select_openai_profiles:
                resolver = CredentialResolver(context)
                presence = resolver.presence("openai")
                if presence.present:
                    actions["credential"] = {
                        "provider": "openai",
                        "present": True,
                        "source": presence.source,
                    }
                elif resolver.keyring.available():
                    credential_storage = "keyring"
                    configure_openai = True
                    print("The credential will be stored in your OS credential store.")
                else:
                    print(
                        "No OS credential-store backend is available in this "
                        "installation. You may use an owner-only workspace secret "
                        "file or manage NYC_HOUSING_OPENAI_API_KEY yourself."
                    )
                    try:
                        reply = input(
                            "Store the credential in the owner-only file? [y/N] "
                        )
                    except EOFError:
                        reply = "n"
                    if reply.strip().lower() in {"y", "yes"}:
                        credential_storage = "file"
                        configure_openai = True
                    else:
                        actions["credential"] = {
                            "provider": "openai",
                            "present": False,
                            "source": "environment_instructions",
                        }
                        print(
                            "Set NYC_HOUSING_OPENAI_API_KEY in the environment "
                            "before starting model-backed actions."
                        )
        if configure_openai:
            value = validate_credential(
                "openai",
                getpass.getpass("Enter openai credential (input hidden): "),
            )
            CredentialResolver(context).writable(credential_storage).set(
                "openai", value
            )
            actions["credential"] = {
                "provider": "openai",
                "present": True,
                "source": (
                    "keyring" if credential_storage == "keyring" else "secret_file"
                ),
            }
        if select_openai_profiles:
            settings = context.settings
            if (
                args.answer_profile is None
                and get_profile(settings.answer_profile).provider == "fake"
            ):
                settings = replace(settings, answer_profile="openai-answer-luna-v1")
            if (
                args.embedding_profile is None
                and get_profile(settings.embedding_profile).provider == "fake"
            ):
                settings = replace(
                    settings,
                    embedding_profile="openai-embedding-3-small-v1",
                )
            context = replace(context, settings=settings.validate())
            save_local_settings(context.paths, context.settings)
            actions["profiles"] = {
                "answer": context.settings.answer_profile,
                "embedding": context.settings.embedding_profile,
            }
        if install_core:
            runner = CorpusMaintenanceJobs(context, storage)
            try:
                job = runner.submit("install")
                completed = runner.wait(job.id)
            finally:
                runner.close()
            if completed.state.value == "cancelled":
                raise OperationCancelled("Core installation was cancelled.")
            if completed.state.value != "succeeded":
                if completed.error_code == "corpus_validation_failed":
                    raise CorpusValidationError(
                        completed.error_message or "Core installation failed."
                    )
                raise SourceDownloadError(
                    completed.error_message or "Core installation failed."
                )
            actions["core_install"] = {
                "job_id": job.id,
                "generation_id": completed.resume.get("generation_id"),
                "readiness": CorpusService(storage).status().readiness,
            }
    finally:
        storage.close()
    payload = _status_payload(context)
    payload["actions"] = actions
    if install_core:
        payload["message"] = (
            "Local workspace initialized and the free text-only core corpus installed. "
            "No paid request was run."
        )
    else:
        payload["message"] = (
            "Local workspace initialized. No paid request or source download was run."
        )
    _write(payload, as_json=args.json)
    if context.detected_legacy_environment and not args.json:
        names = ", ".join(context.detected_legacy_environment)
        print(
            "Ignored legacy environment settings in local mode: " + names,
            file=sys.stderr,
        )
    return 0


def _status(context: WorkspaceContext, *, as_json: bool) -> int:
    _write(_status_payload(context), as_json=as_json)
    return 0


def _status_payload(context: WorkspaceContext) -> dict[str, object]:
    if context.paths.corpus_database.exists() and context.paths.state_database.exists():
        storage = LocalStorage.open(context.paths)
        try:
            return workspace_status(context, storage)
        finally:
            storage.close()
    return {
        "status": (
            "incompatible_partial_storage"
            if context.paths.corpus_database.exists()
            or context.paths.state_database.exists()
            else "not_initialized"
        ),
        "application_version": __version__,
        "workspace_id": context.settings.workspace_id,
        "data_dir": str(context.paths.root),
        "config_file": str(context.paths.config_file),
        "offline": context.settings.offline,
        "schema_versions": {"corpus": None, "state": None},
        "legal_corpus": {
            "readiness": "not_installed",
            "active_generation_id": None,
            "source_count": 0,
            "chunk_count": 0,
            "embedding_ready_count": 0,
        },
        "capabilities": {
            "keyless_search": False,
            "model_answers": False,
            "synthetic_answer_demo": False,
            "property_lookup": "cache_only" if context.settings.offline else "live",
            "citywide_hpd_analytics": False,
        },
    }


def _doctor(context: WorkspaceContext, *, online: bool, as_json: bool) -> int:
    storage = None
    if context.paths.corpus_database.exists() and context.paths.state_database.exists():
        storage = LocalStorage.open(context.paths)
    try:
        payload = doctor_checks(context, storage, online=online)
    finally:
        if storage:
            storage.close()
    _write(payload, as_json=as_json)
    return 0 if payload["status"] == "ok" else EXIT_UNAVAILABLE_DEPENDENCY


def _diagnostics(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = None
    if context.paths.corpus_database.exists() and context.paths.state_database.exists():
        storage = LocalStorage.open(context.paths)
    try:
        payload = export_redacted_diagnostics(
            context,
            storage,
            Path(args.path),
            replace_existing=args.replace,
        )
    finally:
        if storage:
            storage.close()
    _write(payload, as_json=args.json)
    return 0


def _serve(context: WorkspaceContext, args: argparse.Namespace) -> int:
    if not context.initialized:
        print("Workspace is not initialized; run nyc-housing setup.", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION
    port = args.port or context.settings.port
    if not 1 <= port <= 65535:
        print("Port must be between 1 and 65535.", file=sys.stderr)
        return EXIT_INVALID_CONFIGURATION

    from app.local_app import create_local_app

    application = create_local_app(context)
    base_url = f"http://127.0.0.1:{port}/"
    launch_url = f"{base_url}#launch={application.state.launch_token}"
    print(f"Local workspace: {base_url}")
    if context.settings.open_browser and not args.no_browser:
        webbrowser.open(launch_url)
    else:
        print(f"One-time launch code: {application.state.launch_token}")

    import uvicorn

    uvicorn.run(application, host="127.0.0.1", port=port, workers=1)
    return 0


def _sources(context: WorkspaceContext, *, as_json: bool) -> int:
    storage = _initialized_storage(context)
    try:
        service = CorpusService(storage)
        status = service.status()
        payload = {
            "active_generation_id": status.active_generation_id,
            "readiness": status.readiness,
            "sources": service.source_statuses(),
        }
        _write(payload, as_json=as_json)
        return 0
    finally:
        storage.close()


def _corpus(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    try:
        service = CorpusService(storage)
        command = args.corpus_command
        if command in {"install", "update"}:
            source = None if command == "install" else args.source
            runner = CorpusMaintenanceJobs(context, storage)
            try:
                job = runner.submit(
                    command,
                    source=source,
                    allow_partial=(
                        args.allow_partial if command == "update" else False
                    ),
                )
                completed = runner.wait(job.id)
            finally:
                runner.close()
            if completed.state.value == "cancelled":
                raise OperationCancelled("Corpus operation was cancelled.")
            if completed.state.value != "succeeded":
                if completed.error_code == "corpus_validation_failed":
                    raise CorpusValidationError(
                        completed.error_message or "Corpus validation failed."
                    )
                raise SourceDownloadError(
                    completed.error_message or "Corpus operation failed."
                )
            _write(
                {
                    "status": "succeeded",
                    "job_id": job.id,
                    "generation_id": completed.resume.get("generation_id"),
                    "readiness": service.status().readiness,
                },
                as_json=args.json,
            )
            return 0
        if command == "verify":
            result = (
                service.verify(args.generation)
                if args.generation
                else service.verify_all_retained()
            )
            _write(result, as_json=args.json)
            return 0
        if command == "activate":
            job, _result = run_corpus_mutation(
                storage,
                "corpus_activate",
                lambda: service.activate(
                    args.generation_id,
                    allow_partial=args.allow_partial,
                ),
            )
            _write(
                {
                    "status": "active",
                    "job_id": job.id,
                    "generation_id": args.generation_id,
                },
                as_json=args.json,
            )
            return 0
        if command == "rollback":
            job, generation = run_corpus_mutation(
                storage,
                "corpus_rollback",
                service.rollback,
            )
            _write(
                {
                    "status": "active",
                    "job_id": job.id,
                    "generation_id": generation,
                },
                as_json=args.json,
            )
            return 0
        if command == "import-artifact":
            manifest = service.manifests.get(args.source)
            if manifest is None:
                raise CorpusValidationError(f"Unknown source: {args.source}")
            artifact_path = Path(args.path).expanduser().resolve()
            try:
                size = artifact_path.stat().st_size
                if not artifact_path.is_file():
                    raise OSError("not a regular file")
                if size > 512 * 1024**2:
                    raise ValueError(
                        "Manual source artifact exceeds the 512 MiB safety limit."
                    )
                content = artifact_path.read_bytes()
            except OSError as exc:
                raise CorpusValidationError(
                    "Manual source artifact could not be read."
                ) from exc
            source_url = args.source_url or manifest.source_url
            if not source_url.startswith("https://"):
                raise CorpusValidationError("Manual source URL must use HTTPS.")
            content_type = args.content_type or mimetypes.guess_type(artifact_path)[0]
            job, generation = run_corpus_mutation(
                storage,
                "corpus_import_artifact",
                lambda: service.install_artifacts(
                    [
                        SourceArtifact(
                            slug=args.source,
                            content=content,
                            source_url=source_url,
                            content_type=content_type,
                            retrieved_at=datetime.now(UTC),
                        )
                    ],
                    activate=not args.no_activate,
                    allow_partial=args.allow_partial,
                ),
            )
            _write(
                {
                    "status": "imported",
                    "job_id": job.id,
                    "generation_id": generation,
                    "source": args.source,
                    "source_url": source_url,
                    "size_bytes": size,
                    "activated": not args.no_activate,
                },
                as_json=args.json,
            )
            return 0
        if command in {"bundle", "import"}:
            bundles = CanonicalBundleService(storage)
            if command == "import":
                _job, summary = run_corpus_mutation(
                    storage,
                    "corpus_import",
                    lambda: bundles.import_bundle(
                        Path(args.path),
                        activate=not args.no_activate,
                        allow_partial=args.allow_partial,
                    ),
                )
            elif args.bundle_command == "export":
                summary = bundles.export(Path(args.path), args.generation)
            elif args.bundle_command == "inspect":
                summary = bundles.inspect(Path(args.path))
            else:
                _job, summary = run_corpus_mutation(
                    storage,
                    "corpus_import",
                    lambda: bundles.import_bundle(
                        Path(args.path),
                        activate=not args.no_activate,
                        allow_partial=args.allow_partial,
                    ),
                )
            _write(asdict(summary), as_json=args.json)
            return 0
        if command == "index":
            profile = get_configured_profile(context.settings, ProfileKind.EMBEDDING)
            runner = CorpusMaintenanceJobs(context, storage)
            estimate, _paid, _credential_source = runner.estimate_index()
            estimate_payload = asdict(estimate)
            estimate_payload["estimated_cost_usd"] = str(estimate.estimated_cost_usd)
            if args.estimate_only:
                _write(estimate_payload, as_json=args.json)
                runner.close()
                return 0
            if args.max_cost_usd is not None:
                ceiling = args.max_cost_usd
                if estimate.estimated_cost_usd > ceiling:
                    _write(
                        {
                            **estimate_payload,
                            "status": "cost_ceiling_exceeded",
                            "max_cost_usd": str(ceiling),
                        },
                        as_json=args.json,
                    )
                    runner.close()
                    return EXIT_BUDGET_DENIED
            if profile.paid and not args.approve_cost:
                _write(
                    {**estimate_payload, "status": "approval_required"},
                    as_json=args.json,
                )
                runner.close()
                return EXIT_UNAVAILABLE_DEPENDENCY
            if profile.paid and args.max_cost_usd is None:
                _write(
                    {**estimate_payload, "status": "cost_ceiling_required"},
                    as_json=args.json,
                )
                runner.close()
                return EXIT_BUDGET_DENIED
            try:
                job = runner.submit(
                    "index",
                    approve_cost=args.approve_cost,
                    max_cost_usd=args.max_cost_usd,
                    batch_size=args.batch_size,
                )
                completed = runner.wait(job.id)
            finally:
                runner.close()
            if completed.state.value == "cancelled":
                raise OperationCancelled("Corpus indexing was cancelled.")
            if completed.state.value != "succeeded":
                if completed.error_code == "budget_denied":
                    _write(
                        {
                            **estimate_payload,
                            "status": "budget_denied",
                            "error": completed.error_message,
                        },
                        as_json=args.json,
                    )
                    return EXIT_BUDGET_DENIED
                if completed.error_code == "corpus_validation_failed":
                    raise CorpusValidationError(
                        completed.error_message or "Corpus indexing failed."
                    )
                raise ProviderExecutionError(
                    completed.error_message or "Corpus indexing failed."
                )
            _write(
                {
                    "job_id": job.id,
                    "generation_id": completed.resume.get("generation_id"),
                    "profile_id": completed.resume.get("profile_id"),
                    "embedded_chunks": completed.resume.get("embedded_chunks"),
                    "reused_chunks": completed.resume.get("reused_chunks"),
                },
                as_json=args.json,
            )
            return 0
        return EXIT_INVALID_CONFIGURATION
    finally:
        storage.close()


def _search(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    try:
        response = LocalSearch(storage).search(
            args.query,
            filters=LocalSearchFilters(
                source_slug=args.source,
                source_type=args.source_type,
                jurisdiction=args.jurisdiction,
            ),
            limit=args.limit,
        )
        payload = {
            "generation_id": response.generation_id,
            "method": response.method,
            "semantic_status": response.semantic_status,
            "results": [asdict(result) for result in response.results],
        }
        _write(payload, as_json=args.json)
        return 0
    finally:
        storage.close()


def _jobs(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    try:
        service = JobService(storage.state_engine)
        service.recover_interrupted()
        if args.jobs_command == "list":
            records = service.list()
            payload = {"jobs": [_job_payload(record) for record in records]}
        elif args.jobs_command == "resume":
            record = service.get(args.job_id)
            if record.job_type in {
                "corpus_install",
                "corpus_update",
                "corpus_index",
            }:
                runner = CorpusMaintenanceJobs(context, storage)
            elif record.job_type == "property_complete_export":
                runner = PropertyExportJobs(context, storage)
            else:
                raise InvalidJobTransition(
                    "Only corpus and complete-property maintenance jobs can be "
                    "resumed; interactive answers require explicit resubmission."
                )
            try:
                runner.resume(args.job_id)
                payload = _job_payload(runner.wait(args.job_id))
            finally:
                runner.close()
        else:
            payload = _job_payload(service.request_cancel(args.job_id))
        _write(payload, as_json=args.json)
        return 0
    finally:
        storage.close()


def _job_payload(record) -> dict[str, object]:
    return {
        "id": record.id,
        "job_type": record.job_type,
        "target_id": record.target_id,
        "state": record.state.value,
        "stage": record.stage,
        "progress_current": record.progress_current,
        "progress_total": record.progress_total,
        "retryable": record.retryable,
        "resume": record.resume,
        "error_code": record.error_code,
        "error_message": record.error_message,
        "updated_at": record.updated_at.isoformat(),
    }


def _credentials(context: WorkspaceContext, args: argparse.Namespace) -> int:
    if not context.initialized:
        raise LocalSettingsError("Workspace is not initialized; run setup first.")
    resolver = CredentialResolver(context)
    slots = configured_credential_slots(context.settings)
    if args.credentials_command == "status":
        payload = {
            "credentials": [asdict(resolver.presence(provider)) for provider in slots]
        }
    elif args.credentials_command == "set":
        if args.provider not in slots:
            raise CredentialStoreError(
                "Credential slot is not used by a configured provider profile."
            )
        value = validate_credential(
            args.provider,
            getpass.getpass(f"Enter {args.provider} credential (input hidden): "),
        )
        resolver.writable(args.storage).set(args.provider, value)
        payload = {
            "provider": args.provider,
            "present": True,
            "source": "keyring" if args.storage == "keyring" else "secret_file",
        }
    elif args.credentials_command == "delete":
        if args.provider not in slots:
            raise CredentialStoreError(
                "Credential slot is not used by a configured provider profile."
            )
        resolver.writable(args.storage).delete(args.provider)
        payload = {
            "provider": args.provider,
            "present": False,
            "source": None,
        }
    else:
        storage = _initialized_storage(context)
        try:
            result = run_credential_validation(
                context,
                storage,
                args.provider,
                approve_cost=args.approve_cost,
                max_cost_usd=args.max_cost_usd,
            )
            payload = asdict(result)
            payload["cost_usd"] = str(result.cost_usd)
        finally:
            storage.close()
    _write(payload, as_json=args.json)
    return 0


def _profiles(context: WorkspaceContext, args: argparse.Namespace) -> int:
    if not context.initialized:
        raise LocalSettingsError("Workspace is not initialized; run setup first.")
    if args.profiles_command == "list":
        payload = {
            "selected": {
                "answer": context.settings.answer_profile,
                "embedding": context.settings.embedding_profile,
            },
            "profiles": [
                {
                    "id": profile.id,
                    "selection_id": selection_id,
                    "version": profile.version,
                    "kind": profile.kind.value,
                    "provider": profile.provider,
                    "credential_slot": profile.credential_slot,
                    "model": profile.model,
                    "endpoint": profile.endpoint,
                    "dimension": profile.dimension,
                    "token_estimator": profile.token_estimator,
                    "request_timeout_seconds": profile.request_timeout_seconds,
                    "max_attempts": profile.max_attempts,
                    "pricing_verified": profile.pricing_verified,
                    "price_effective_date": profile.price_effective_date,
                    "price_source": profile.price_source,
                    "stores_response": profile.stores_response,
                    "compatibility_verified": profile.compatibility_verified,
                }
                for selection_id, profile in configured_profile_entries(
                    context.settings
                )
            ],
        }
    elif args.profiles_command == "select":
        kind = ProfileKind(args.kind)
        get_profile(args.profile_id, kind=kind)
        field = "answer_profile" if kind == ProfileKind.ANSWER else "embedding_profile"
        settings = replace(context.settings, **{field: args.profile_id}).validate()
        save_local_settings(context.paths, settings)
        payload = {"kind": kind.value, "selected": args.profile_id}
    elif args.profiles_command == "configure-endpoint":
        kind = ProfileKind(args.kind)
        selection_id = (
            context.settings.answer_profile
            if kind == ProfileKind.ANSWER
            else context.settings.embedding_profile
        )
        base = get_profile(selection_id, kind=kind)
        if base.provider == "fake":
            raise ValueError(
                "Select a non-synthetic profile before configuring an endpoint."
            )
        supplied_price_metadata = any(
            value is not None
            for value in (
                args.input_price,
                args.output_price,
                args.price_effective_date,
                args.price_source,
            )
        )
        if args.pricing_unknown and supplied_price_metadata:
            raise ValueError(
                "--pricing-unknown cannot be combined with price metadata."
            )
        if not args.pricing_unknown:
            required = [
                args.input_price,
                args.price_effective_date,
                args.price_source,
            ]
            if kind == ProfileKind.ANSWER:
                required.append(args.output_price)
            if any(value is None for value in required):
                raise ValueError(
                    "Known pricing requires input price, HTTPS price source, "
                    "effective date, and an output price for answer profiles."
                )
        overrides = dict(context.settings.profile_overrides)
        overrides[selection_id] = {
            "endpoint": args.endpoint,
            "auth_slot": args.auth_slot or base.credential_slot or base.provider,
            "input_usd_per_million": (
                None if args.pricing_unknown else str(args.input_price)
            ),
            "output_usd_per_million": (
                str(args.output_price) if args.output_price is not None else None
            ),
            "price_effective_date": (
                None if args.pricing_unknown else args.price_effective_date
            ),
            "price_source": None if args.pricing_unknown else args.price_source,
            "stores_response": args.stores_response == "yes",
        }
        checks = dict(context.settings.profile_compatibility_checks)
        checks.pop(selection_id, None)
        settings = replace(
            context.settings,
            profile_overrides=overrides,
            profile_compatibility_checks=checks,
        ).validate()
        save_local_settings(context.paths, settings)
        profile = get_configured_profile(settings, kind)
        payload = {
            "kind": kind.value,
            "selection_id": selection_id,
            "effective_profile_id": profile.id,
            "endpoint": profile.endpoint,
            "credential_slot": profile.credential_slot,
            "compatibility_verified": False,
            "stores_response": profile.stores_response,
            "pricing_verified": profile.pricing_verified,
            "cost_policy": (
                "metered_with_usd_caps"
                if profile.pricing_verified
                else "manual_unknown_cost_outside_usd_caps"
            ),
            "next_step": f"profiles check {kind.value} --estimate-only",
        }
    elif args.profiles_command == "clear-endpoint":
        kind = ProfileKind(args.kind)
        selection_id = (
            context.settings.answer_profile
            if kind == ProfileKind.ANSWER
            else context.settings.embedding_profile
        )
        overrides = dict(context.settings.profile_overrides)
        checks = dict(context.settings.profile_compatibility_checks)
        removed = overrides.pop(selection_id, None) is not None
        checks.pop(selection_id, None)
        settings = replace(
            context.settings,
            profile_overrides=overrides,
            profile_compatibility_checks=checks,
        ).validate()
        save_local_settings(context.paths, settings)
        payload = {
            "kind": kind.value,
            "selection_id": selection_id,
            "removed": removed,
        }
    else:
        kind = ProfileKind(args.kind)
        estimate = profile_compatibility_estimate(context, kind)
        if args.estimate_only:
            payload = asdict(estimate)
            payload["estimated_cost_usd"] = (
                str(estimate.estimated_cost_usd)
                if estimate.estimated_cost_usd is not None
                else None
            )
            if not estimate.cost_known:
                payload["warning"] = (
                    "Provider price is unknown; this one-off request is outside "
                    "USD budget caps."
                )
        else:
            storage = _initialized_storage(context)
            try:
                result = run_profile_compatibility_check(
                    context,
                    storage,
                    kind,
                    approve_cost=args.approve_cost,
                    max_cost_usd=args.max_cost_usd,
                    allow_unknown_cost=args.allow_unknown_cost,
                )
            finally:
                storage.close()
            payload = asdict(result)
            payload["cost_usd"] = (
                str(result.cost_usd) if result.cost_usd is not None else None
            )
            if not result.cost_known:
                payload["warning"] = (
                    "Unknown-cost request completed outside USD budget caps."
                )
            payload["restart_required"] = True
    _write(payload, as_json=args.json)
    return 0


def _usage(context: WorkspaceContext, *, as_json: bool) -> int:
    storage = _initialized_storage(context)
    try:
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal(context.settings.monthly_budget_usd),
            timezone=context.settings.budget_timezone,
        )
        payload = {
            "month": summary.month,
            "timezone": summary.timezone,
            "cap_usd": str(summary.cap_usd),
            "settled_usd": str(summary.settled_usd),
            "reserved_usd": str(summary.reserved_usd),
            "uncertain_usd": str(summary.uncertain_usd),
            "remaining_usd": str(summary.remaining_usd),
            "unknown_cost_attempts": summary.unknown_cost_attempts,
            "unknown_cost_in_flight": summary.unknown_cost_in_flight,
            "unknown_cost_uncertain": summary.unknown_cost_uncertain,
            "history_pruned_before": summary.history_pruned_before,
            "scope": "this local installation only",
            "max_concurrent_paid_requests": (
                context.settings.max_concurrent_paid_requests
            ),
        }
        _write(payload, as_json=as_json)
        return 0
    finally:
        storage.close()


def _ask(context: WorkspaceContext, args: argparse.Namespace) -> int:
    deadline_seconds = (
        args.deadline
        if args.deadline is not None
        else context.settings.answer_deadline_seconds
    )
    if not 5 <= deadline_seconds <= 300:
        raise ValueError("Answer deadline must be between 5 and 300 seconds.")
    storage = _initialized_storage(context)
    gateway = ProviderGateway(context, UsageLedger(storage))
    jobs_service = JobService(storage.state_engine)
    job = jobs_service.create("answer", f"interactive:{uuid.uuid4()}")
    worker_id = f"cli-{os.getpid()}"
    jobs_service.claim(
        job.id, worker_id, lease_seconds=max(60, int(deadline_seconds) + 5)
    )
    try:
        result = LocalAnswerService(
            context,
            storage,
            gateway,
            CredentialResolver(context),
        ).answer(
            args.question,
            filters=LocalSearchFilters(source_slug=args.source),
            limit=args.limit,
            deadline=Deadline.after(deadline_seconds),
            operation_id=job.id,
            allow_unknown_cost=args.allow_unknown_cost,
        )
        jobs_service.succeed(job.id, worker_id)
        payload = asdict(result)
        payload["job_id"] = job.id
        _write(payload, as_json=args.json)
        return 0
    except Exception as exc:
        jobs_service.fail(
            job.id,
            worker_id,
            error_code="answer_failed",
            error_message=str(exc),
            retryable=False,
        )
        raise
    finally:
        gateway.close()
        storage.close()


def _debug_answer(context: WorkspaceContext, args: argparse.Namespace) -> int:
    deadline_seconds = (
        args.deadline
        if args.deadline is not None
        else context.settings.answer_deadline_seconds
    )
    if not 5 <= deadline_seconds <= 300:
        raise ValueError("Answer deadline must be between 5 and 300 seconds.")
    storage = _initialized_storage(context)
    gateway = ProviderGateway(context, UsageLedger(storage))
    jobs_service = JobService(storage.state_engine)
    job = jobs_service.create("debug_answer", f"interactive:{uuid.uuid4()}")
    worker_id = f"cli-debug-{os.getpid()}"
    jobs_service.claim(
        job.id,
        worker_id,
        lease_seconds=max(60, int(deadline_seconds) + 5),
    )
    try:
        answer_profile = get_configured_profile(context.settings, ProfileKind.ANSWER)
        embedding_profile = get_configured_profile(
            context.settings, ProfileKind.EMBEDDING
        )
        result = LocalAnswerService(
            context,
            storage,
            gateway,
            CredentialResolver(context),
        ).answer(
            args.question,
            filters=LocalSearchFilters(source_slug=args.source),
            limit=args.limit,
            deadline=Deadline.after(deadline_seconds),
            operation_id=job.id,
            allow_unknown_cost=args.allow_unknown_cost,
        )
        jobs_service.succeed(job.id, worker_id)
        status = workspace_status(context, storage)
        _write(
            {
                "status": "complete",
                "mode": "debug_answer",
                "job_id": job.id,
                "profiles": {
                    "answer": {
                        "id": answer_profile.id,
                        "provider": answer_profile.provider,
                        "model": answer_profile.model,
                        "paid": answer_profile.paid,
                        "price_effective_date": answer_profile.price_effective_date,
                    },
                    "embedding": {
                        "id": embedding_profile.id,
                        "provider": embedding_profile.provider,
                        "model": embedding_profile.model,
                        "dimension": embedding_profile.dimension,
                        "paid": embedding_profile.paid,
                        "price_effective_date": embedding_profile.price_effective_date,
                    },
                },
                "corpus": status["legal_corpus"],
                "credential_presence": status["credentials"],
                "usage": status["usage"],
                "result": asdict(result),
                "privacy": (
                    "No credential, raw provider response, or hidden prompt is "
                    "included; this explicit terminal output includes the supplied "
                    "question and answer."
                ),
            },
            as_json=args.json,
        )
        return 0
    except Exception as exc:
        jobs_service.fail(
            job.id,
            worker_id,
            error_code="debug_answer_failed",
            error_message=str(exc),
            retryable=False,
        )
        raise
    finally:
        gateway.close()
        storage.close()


def _property(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    credential, _source = CredentialResolver(context).resolve("socrata")
    connector = HpdSocrataConnector(context.network, app_token=credential)
    repository = CachedPropertyRepository(
        storage,
        connector,
        max_bytes=context.settings.property_cache_max_mb * 1024**2,
        retention_days=context.settings.property_cache_retention_days,
    )
    try:
        if args.property_command == "cache-clear":
            _write({"removed_artifacts": repository.clear()}, as_json=args.json)
            return 0
        query = PropertyQuery(
            building_id=args.building_id,
            registration_id=args.registration_id,
            house_number=args.house_number,
            street_name=args.street_name,
            borough=args.borough,
            zip_code=args.zip_code,
            violation_class=args.violation_class,
            status=args.status,
            inspection_date_from=args.inspection_date_from,
            inspection_date_to=args.inspection_date_to,
            limit=args.limit,
            continuation=getattr(args, "continuation", None),
        )
        if args.property_command == "export":
            result = export_complete_property_result(
                repository,
                ResearchExporter(context.paths),
                JobService(storage.state_engine),
                query,
                max_pages=args.max_pages,
                deadline=Deadline.after(args.deadline),
            )
            _write(asdict(result), as_json=args.json)
            return 0
        response = repository.search(
            query,
            refresh=args.refresh,
            deadline=Deadline.after(15),
        )
        payload = asdict(response)
        payload["fetched_at"] = response.fetched_at.isoformat()
        for field in (
            "source_update_time",
            "fetch_started_at",
            "fetch_completed_at",
        ):
            value = getattr(response, field)
            payload[field] = value.isoformat() if value is not None else None
        _write(payload, as_json=args.json)
        return 0
    finally:
        connector.close()
        storage.close()


def _backup(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    try:
        summary = WorkspaceBackupService(storage).create(
            Path(args.path),
            include_property_cache=args.include_property_cache,
        )
        _write(asdict(summary), as_json=args.json)
        return 0
    finally:
        storage.close()


def _restore(args: argparse.Namespace) -> int:
    summary = restore_backup(Path(args.archive), Path(args.destination))
    _write(asdict(summary), as_json=args.json)
    return 0


def _maintenance(context: WorkspaceContext, args: argparse.Namespace) -> int:
    if args.maintenance_command != "prune":
        return EXIT_INVALID_CONFIGURATION
    storage = _initialized_storage(context)
    try:
        result = WorkspaceRetentionService(storage, context.settings).run(
            apply=args.apply
        )
        _write(result.as_dict(), as_json=args.json)
        return 0
    finally:
        storage.close()


def _legacy(args: argparse.Namespace) -> int:
    legacy_command = getattr(args, "legacy_command", None)
    migrate_command = getattr(args, "migrate_command", None)
    if legacy_command != "export" and migrate_command != "export-legacy":
        return EXIT_INVALID_CONFIGURATION
    database_url = database_url_from_environment(args.database_url_env)
    summary = LegacyCorpusExporter(
        database_url,
        Path(args.artifact_root),
    ).export(Path(args.path))
    payload = asdict(summary)
    _write(payload, as_json=args.json)
    return 0


def _migration_preflight(context: WorkspaceContext, args: argparse.Namespace) -> int:
    if not context.initialized:
        raise LocalSettingsError("Workspace is not initialized; run setup first.")
    storage = LocalStorage.open(context.paths)
    try:
        versions = storage.versions()
    finally:
        storage.close()
    supported = {
        "corpus": CORPUS_SCHEMA_VERSION,
        "state": STATE_SCHEMA_VERSION,
    }
    compatible = versions == supported
    payload = {
        "status": "compatible" if compatible else "migration_unavailable",
        "read_only": True,
        "current_schema_versions": versions,
        "supported_schema_versions": supported,
        "migration_required": not compatible,
        "migration_available": False,
        "backup_required_before_migration": not compatible,
        "next_action": (
            "No local schema migration is required for this release."
            if compatible
            else "Keep the current code and workspace. This release has no reviewed "
            "migration path for these schema versions; do not downgrade or edit "
            "schema metadata."
        ),
    }
    _write(payload, as_json=args.json)
    return 0 if compatible else EXIT_INVALID_CONFIGURATION


def _evaluate_retrieval(context: WorkspaceContext, args: argparse.Namespace) -> int:
    storage = _initialized_storage(context)
    try:
        if getattr(args, "answers", False):
            case_file = Path(args.legal_case_file) if args.legal_case_file else None
            if args.estimate_only:
                estimate = estimate_answer_evaluation(
                    context,
                    case_file=case_file,
                )
                _write(answer_evaluation_payload(estimate), as_json=args.json)
                return 0
            report = run_answer_evaluation(
                context,
                storage,
                approve_cost=args.approve_cost,
                max_cost_usd=args.max_cost_usd,
                case_file=case_file,
            )
            _write(answer_evaluation_payload(report), as_json=args.json)
            return 0 if report.automated_checks_passed else EXIT_VALIDATION_FAILED
        if getattr(args, "estimate_only", False):
            raise ValueError("--estimate-only requires --answers.")
        cases = load_retrieval_cases(Path(args.case_file) if args.case_file else None)
        evaluation = evaluate_retrieval(
            LocalSearch(storage),
            cases,
            k=args.k,
            minimum_recall=args.minimum_recall,
            minimum_mrr=args.minimum_mrr,
        )
        payload = asdict(evaluation)
        _write(payload, as_json=args.json)
        return 0 if evaluation.passed else EXIT_VALIDATION_FAILED
    finally:
        storage.close()


def _benchmark(args: argparse.Namespace) -> int:
    result = run_synthetic_benchmark(
        chunk_count=args.chunks,
        dimension=args.dimension,
        runs=args.runs,
    )
    _write(benchmark_as_dict(result), as_json=args.json)
    required = ("startup", "keyword_p95", "hybrid_p95")
    return (
        0 if all(result.passes[name] for name in required) else EXIT_VALIDATION_FAILED
    )


def _update_check(context: WorkspaceContext, args: argparse.Namespace) -> int:
    result = check_github_release(args.repository, context.network)
    _write(asdict(result), as_json=args.json)
    return 0


def _initialized_storage(context: WorkspaceContext) -> LocalStorage:
    if not context.initialized:
        raise LocalSettingsError("Workspace is not initialized; run setup first.")
    storage = LocalStorage.open(context.paths)
    versions = storage.versions()
    if versions != {"corpus": 1, "state": 1}:
        storage.close()
        raise SchemaVersionError(
            "Local storage is not initialized or has incompatible schema versions."
        )
    return storage


def _write(payload: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    for key, value in payload.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for child_key, child_value in value.items():
                print(f"  {child_key}: {child_value}")
        else:
            print(f"{key}: {value}")


if __name__ == "__main__":
    raise SystemExit(main())
