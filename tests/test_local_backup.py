import json
import zipfile
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.corpus.service import CorpusService, SourceArtifact
from app.maintenance.backup import BackupError, WorkspaceBackupService, restore_backup
from app.retrieval.local import LocalSearch
from app.security.local_session import LocalSessionService
from app.storage.database import LocalStorage
from app.usage.ledger import UsageLedger
from app.workspace.context import WorkspaceContext


def _prepared(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "source", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Grounds for summary proceedings.",
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    reservation = UsageLedger(storage).reserve(
        operation_id="operation",
        attempt_id="attempt",
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal("0.10"),
        monthly_cap_usd=Decimal("15"),
        per_operation_cap_usd=Decimal("2"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
    )
    UsageLedger(storage).settle(
        reservation,
        actual_usd=Decimal("0.05"),
        input_tokens=1,
        output_tokens=1,
        price_snapshot={"fixture": True},
        provider="fixture",
        profile_id="fixture-v1",
    )
    return context, storage


def test_sanitized_backup_restores_corpus_and_usage_to_new_workspace(tmp_path) -> None:
    context, storage = _prepared(tmp_path)
    launch_token = LocalSessionService(storage).begin_process()
    credential = "sk-private-fixture-value"
    (context.paths.root / "credentials.json").write_text(
        json.dumps({"openai": credential}), encoding="utf-8"
    )
    (context.paths.root / "credentials.json").chmod(0o600)
    archive = tmp_path / "workspace-backup.nychousing-backup"
    try:
        summary = WorkspaceBackupService(storage).create(archive)
        assert summary.secrets_included is False
    finally:
        storage.close()

    with zipfile.ZipFile(archive) as backup:
        names = backup.namelist()
        content = b"".join(backup.read(name) for name in names)
    assert "credentials.json" not in names
    assert credential.encode() not in content
    assert launch_token.encode() not in content

    destination = tmp_path / "restored"
    restore_backup(archive, destination)
    restored_context = WorkspaceContext.from_options(destination, environment={})
    assert restored_context.settings.workspace_id != context.settings.workspace_id
    restored = LocalStorage.open(restored_context.paths)
    try:
        result = LocalSearch(restored).search("RPAPL 711")
        assert result.results[0].citation == "RPAPL § 711"
        usage = UsageLedger(restored).summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert usage.settled_usd == Decimal("0.05000000")
    finally:
        restored.close()


def test_corrupt_backup_does_not_create_destination(tmp_path) -> None:
    context, storage = _prepared(tmp_path)
    archive = tmp_path / "valid.backup"
    try:
        WorkspaceBackupService(storage).create(archive)
    finally:
        storage.close()
    corrupt = tmp_path / "corrupt.backup"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name == "databases/corpus.sqlite3":
                content += b"corrupt"
            target.writestr(name, content)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(BackupError, match="verification"):
        restore_backup(corrupt, destination)
    assert not destination.exists()


def test_backup_refuses_active_jobs(tmp_path) -> None:
    _context, storage = _prepared(tmp_path)
    from app.jobs.service import JobService

    try:
        JobService(storage.state_engine).create("corpus_update", "core")
        with pytest.raises(BackupError, match="active jobs"):
            WorkspaceBackupService(storage).create(tmp_path / "blocked.backup")
    finally:
        storage.close()
