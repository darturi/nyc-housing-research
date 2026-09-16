from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from sqlalchemy import select, update

from app.storage.database import LocalStorage
from app.storage.schema import (
    jobs,
    maintenance_state,
    property_cache,
    source_versions,
)
from app.workspace.paths import resolve_workspace_paths

BACKUP_FORMAT = "nyc-housing-workspace-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 4 * 1024**3


class BackupError(RuntimeError):
    pass


@dataclass(frozen=True)
class BackupSummary:
    path: str
    created_at: str
    artifact_count: int
    includes_property_cache: bool
    secrets_included: bool = False


class WorkspaceBackupService:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def create(
        self, destination: Path, *, include_property_cache: bool = False
    ) -> BackupSummary:
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(UTC)
        self._enter_barrier(created_at)
        temporary_root = Path(tempfile.mkdtemp(prefix="nyc-housing-backup-"))
        try:
            corpus_copy = temporary_root / "corpus.sqlite3"
            state_copy = temporary_root / "state.sqlite3"
            _sqlite_backup(self._storage.paths.corpus_database, corpus_copy)
            _sqlite_backup(self._storage.paths.state_database, state_copy)
            _sanitize_state_copy(state_copy, include_property_cache)
            members = {
                "databases/corpus.sqlite3": corpus_copy.read_bytes(),
                "databases/state.sqlite3": state_copy.read_bytes(),
                "settings.json": _sanitized_settings(self._storage),
            }
            artifact_paths = self._artifact_paths(include_property_cache)
            artifact_map = {}
            for index, source in enumerate(artifact_paths, start=1):
                member = f"artifacts/{index:06d}-{source.name}"
                members[member] = source.read_bytes()
                artifact_map[
                    str(source.relative_to(self._storage.paths.root))
                ] = member
            metadata = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_VERSION,
                "created_at": created_at.isoformat(),
                "includes_property_cache": include_property_cache,
                "secrets_included": False,
                "artifact_map": artifact_map,
                "members": {
                    name: {"sha256": _sha256(content), "size_bytes": len(content)}
                    for name, content in sorted(members.items())
                },
            }
            members["backup-manifest.json"] = _json_bytes(metadata)
            _write_zip(destination, members)
            return BackupSummary(
                path=str(destination),
                created_at=created_at.isoformat(),
                artifact_count=len(artifact_paths),
                includes_property_cache=include_property_cache,
            )
        finally:
            shutil.rmtree(temporary_root, ignore_errors=True)
            self._leave_barrier()

    def _enter_barrier(self, now: datetime) -> None:
        connection = self._storage.state_engine.connect()
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            barrier = connection.scalar(
                select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
            )
            if barrier:
                raise BackupError("Another workspace maintenance operation is active.")
            active_job = connection.scalar(
                select(jobs.c.id).where(
                    jobs.c.state.in_(["queued", "running", "cancel_requested"])
                )
            )
            if active_job:
                raise BackupError(
                    "Pause or finish active jobs before creating a workspace backup."
                )
            connection.execute(
                update(maintenance_state)
                .where(maintenance_state.c.id == 1)
                .values(active=True, operation="backup", started_at=now)
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _leave_barrier(self) -> None:
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                update(maintenance_state)
                .where(maintenance_state.c.id == 1)
                .values(active=False, operation=None, started_at=None)
            )

    def _artifact_paths(self, include_property_cache: bool) -> list[Path]:
        relatives = []
        with self._storage.corpus_engine.connect() as connection:
            relatives.extend(connection.scalars(select(source_versions.c.artifact_uri)))
        if include_property_cache:
            with self._storage.state_engine.connect() as connection:
                relatives.extend(
                    connection.scalars(select(property_cache.c.response_artifact))
                )
        paths = []
        root = self._storage.paths.artifacts.resolve()
        for relative in sorted(set(relatives)):
            path = (self._storage.paths.root / relative).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise BackupError(
                    "A referenced artifact escapes the workspace."
                ) from exc
            if not path.is_file():
                raise BackupError(f"A referenced artifact is missing: {relative}")
            paths.append(path)
        return paths


def restore_backup(archive: Path, destination: Path) -> BackupSummary:
    archive = archive.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise BackupError("Restore destination must not already exist.")
    manifest, members = _read_backup(archive)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-restore-", dir=destination.parent)
    )
    try:
        (stage / "artifacts").mkdir(parents=True)
        (stage / "corpus.sqlite3").write_bytes(members["databases/corpus.sqlite3"])
        (stage / "state.sqlite3").write_bytes(members["databases/state.sqlite3"])
        settings = json.loads(members["settings.json"])
        settings["workspace_id"] = (
            "local-" + hashlib.sha256(str(destination).encode()).hexdigest()[:20]
        )
        (stage / "settings.json").write_bytes(_json_bytes(settings))
        (stage / "workspace.json").write_bytes(
            _json_bytes(
                {
                    "format_version": 1,
                    "corpus_schema_version": 1,
                    "state_schema_version": 1,
                }
            )
        )
        for relative, member in manifest["artifact_map"].items():
            target = (stage / relative).resolve()
            try:
                target.relative_to((stage / "artifacts").resolve())
            except ValueError as exc:
                raise BackupError("Backup artifact map escapes the workspace.") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(members[member])
        paths = resolve_workspace_paths(
            stage, config_file=stage / "settings.json", environment={}
        )
        storage = LocalStorage.open(paths)
        try:
            if storage.versions() != {"corpus": 1, "state": 1}:
                raise BackupError("Backup schema versions are incompatible.")
        finally:
            storage.close()
        os.replace(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return BackupSummary(
        path=str(destination),
        created_at=manifest["created_at"],
        artifact_count=len(manifest["artifact_map"]),
        includes_property_cache=bool(manifest["includes_property_cache"]),
    )


def _sanitize_state_copy(path: Path, include_property_cache: bool) -> None:
    with sqlite3.connect(path) as connection:
        for table in (
            "local_sessions",
            "launcher_tokens",
            "local_installation",
            "paid_call_leases",
        ):
            connection.execute(f'DELETE FROM "{table}"')
        connection.execute(
            "UPDATE maintenance_state SET active=0, operation=NULL, "
            "started_at=NULL WHERE id=1"
        )
        if not include_property_cache:
            connection.execute("DELETE FROM property_cache")
    with sqlite3.connect(path) as connection:
        connection.execute("VACUUM")


def _sanitized_settings(storage: LocalStorage) -> bytes:
    try:
        payload = json.loads(storage.paths.config_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("Workspace settings could not be backed up.") from exc
    for key in list(payload):
        if any(
            fragment in key.lower()
            for fragment in ("key", "secret", "token", "password")
        ):
            payload.pop(key)
    return _json_bytes(payload)


def _sqlite_backup(source: Path, destination: Path) -> None:
    with (
        sqlite3.connect(source) as source_connection,
        sqlite3.connect(destination) as destination_connection,
    ):
        source_connection.backup(destination_connection)


def _read_backup(path: Path):
    if not path.is_file() or path.stat().st_size > MAX_BACKUP_BYTES:
        raise BackupError("Backup is missing or exceeds the supported size.")
    members = {}
    total = 0
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or "\\" in info.filename
                    or info.is_dir()
                    or stat.S_ISLNK(mode)
                ):
                    raise BackupError("Backup contains an unsafe member.")
                total += info.file_size
                if total > MAX_BACKUP_BYTES:
                    raise BackupError("Expanded backup exceeds the supported size.")
                members[info.filename] = archive.read(info)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError("Backup archive is invalid.") from exc
    try:
        manifest = json.loads(members["backup-manifest.json"])
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BackupError("Backup manifest is invalid.") from exc
    if manifest.get("format") != BACKUP_FORMAT or manifest.get("format_version") != 1:
        raise BackupError("Backup format is unsupported.")
    declared = manifest.get("members", {})
    if set(declared) != set(members) - {"backup-manifest.json"}:
        raise BackupError("Backup member manifest is incomplete.")
    for name, metadata in declared.items():
        if metadata.get("size_bytes") != len(members[name]) or metadata.get(
            "sha256"
        ) != _sha256(members[name]):
            raise BackupError(f"Backup member verification failed: {name}")
    return manifest, members


def _write_zip(destination: Path, members: dict[str, bytes]) -> None:
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".backup-", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, content in sorted(members.items()):
                archive.writestr(member, content)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
