from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from sqlalchemy import select

from app.maintenance.barrier import MaintenanceBarrier
from app.storage.database import LocalStorage
from app.storage.schema import (
    CORPUS_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    jobs,
    property_cache,
    source_versions,
)
from app.workspace.durable import sync_directory, sync_file
from app.workspace.paths import resolve_workspace_paths

BACKUP_FORMAT = "nyc-housing-workspace-backup"
BACKUP_VERSION = 1
MAX_BACKUP_BYTES = 4 * 1024**3
COPY_CHUNK_BYTES = 1024**2
MAX_METADATA_BYTES = 16 * 1024**2
MAX_BACKUP_MEMBERS = 50_000


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
        self._barrier = MaintenanceBarrier(storage.state_engine)

    def create(
        self, destination: Path, *, include_property_cache: bool = False
    ) -> BackupSummary:
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        created_at = datetime.now(UTC)
        self._enter_barrier(created_at)
        temporary_root = None
        try:
            temporary_root = Path(tempfile.mkdtemp(prefix="nyc-housing-backup-"))
            corpus_copy = temporary_root / "corpus.sqlite3"
            state_copy = temporary_root / "state.sqlite3"
            _sqlite_backup(self._storage.paths.corpus_database, corpus_copy)
            _sqlite_backup(self._storage.paths.state_database, state_copy)
            _sanitize_state_copy(state_copy, include_property_cache)
            settings_copy = temporary_root / "settings.json"
            settings_copy.write_bytes(_sanitized_settings(self._storage))
            members = {
                "databases/corpus.sqlite3": corpus_copy,
                "databases/state.sqlite3": state_copy,
                "settings.json": settings_copy,
            }
            artifact_paths = self._artifact_paths(include_property_cache)
            artifact_map = {}
            for index, source in enumerate(artifact_paths, start=1):
                member = f"artifacts/{index:06d}-{source.name}"
                members[member] = source
                artifact_map[
                    source.relative_to(self._storage.paths.root).as_posix()
                ] = member
            metadata = {
                "format": BACKUP_FORMAT,
                "format_version": BACKUP_VERSION,
                "created_at": created_at.isoformat(),
                "includes_property_cache": include_property_cache,
                "secrets_included": False,
                "artifact_map": artifact_map,
            }
            _write_zip(destination, members, metadata)
            return BackupSummary(
                path=str(destination),
                created_at=created_at.isoformat(),
                artifact_count=len(artifact_paths),
                includes_property_cache=include_property_cache,
            )
        finally:
            if temporary_root is not None:
                shutil.rmtree(temporary_root, ignore_errors=True)
            self._leave_barrier()

    def _enter_barrier(self, now: datetime) -> None:
        try:
            self._barrier.enter("backup", now)
        except RuntimeError as exc:
            raise BackupError(str(exc)) from exc

    def _leave_barrier(self) -> None:
        self._barrier.leave()

    def _artifact_paths(self, include_property_cache: bool) -> list[Path]:
        relatives = []
        with self._storage.corpus_engine.connect() as connection:
            relatives.extend(connection.scalars(select(source_versions.c.artifact_uri)))
        with self._storage.state_engine.connect() as connection:
            pending = connection.execute(
                select(jobs.c.resume_json).where(
                    jobs.c.job_type.in_(["resource_add", "resource_replace"]),
                    jobs.c.state.in_(["paused", "failed"]),
                    jobs.c.retryable.is_(True),
                )
            ).scalars()
            for raw in pending:
                stage_id = str(uuid.UUID(json.loads(raw)["stage_id"]))
                relatives.extend(
                    str(
                        (
                            self._storage.paths.artifacts
                            / "resource-staging"
                            / f"{stage_id}{suffix}"
                        ).relative_to(self._storage.paths.root)
                    )
                    for suffix in (".bin", ".json")
                )
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
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-restore-", dir=destination.parent)
    )
    try:
        manifest = _read_backup(archive, destination=stage)
        try:
            settings = json.loads((stage / "settings.json").read_bytes())
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BackupError("Backup settings are invalid.") from exc
        settings["workspace_id"] = (
            "local-" + hashlib.sha256(str(destination).encode()).hexdigest()[:20]
        )
        (stage / "settings.json").write_bytes(_json_bytes(settings))
        paths = resolve_workspace_paths(
            stage, config_file=stage / "settings.json", environment={}
        )
        storage = LocalStorage.open(paths)
        try:
            versions = storage.versions()
            if versions not in (
                {"corpus": CORPUS_SCHEMA_VERSION, "state": STATE_SCHEMA_VERSION},
                {"corpus": 1, "state": 1},
                {"corpus": 1, "state": 2},
                {"corpus": 2, "state": 1},
                {"corpus": 2, "state": 2},
                {"corpus": 3, "state": 1},
            ):
                raise BackupError("Backup schema versions are incompatible.")
        finally:
            storage.close()
        (stage / "workspace.json").write_bytes(
            _json_bytes(
                {
                    "format_version": 1,
                    "corpus_schema_version": versions["corpus"],
                    "state_schema_version": versions["state"],
                }
            )
        )
        for directory, _subdirs, files in os.walk(stage, topdown=False):
            for name in files:
                sync_file(Path(directory) / name)
            sync_directory(Path(directory))
        os.replace(stage, destination)
        sync_directory(destination.parent)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return BackupSummary(
        path=str(destination),
        created_at=manifest["created_at"],
        artifact_count=len(manifest["artifact_map"]),
        includes_property_cache=bool(manifest["includes_property_cache"]),
    )


def _sanitize_state_copy(path: Path, include_property_cache: bool) -> None:
    with closing(sqlite3.connect(path)) as connection:
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
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("VACUUM")


def _sanitized_settings(storage: LocalStorage) -> bytes:
    try:
        if storage.paths.config_file.stat().st_size > MAX_METADATA_BYTES:
            raise BackupError("Workspace settings exceed the supported size.")
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
        closing(sqlite3.connect(source)) as source_connection,
        closing(sqlite3.connect(destination)) as destination_connection,
    ):
        source_connection.backup(destination_connection)
        destination_connection.execute("PRAGMA journal_mode=DELETE")


def _safe_relative(name: str) -> bool:
    member = PurePosixPath(name)
    return bool(name) and not (
        member.is_absolute()
        or ".." in member.parts
        or "\\" in name
        or ":" in name
        or str(member) != name
        or name == "."
    )


def _manifest(archive: zipfile.ZipFile) -> dict:
    infos = archive.infolist()
    names = [info.filename for info in infos]
    if len(infos) > MAX_BACKUP_MEMBERS or len(set(names)) != len(names):
        raise BackupError("Backup contains duplicate or too many members.")
    total = 0
    for info in infos:
        if (
            not _safe_relative(info.filename)
            or info.is_dir()
            or stat.S_ISLNK(info.external_attr >> 16)
        ):
            raise BackupError("Backup contains an unsafe member.")
        total += info.file_size
        if total > MAX_BACKUP_BYTES:
            raise BackupError("Expanded backup exceeds the supported size.")
    try:
        info = archive.getinfo("backup-manifest.json")
        if info.file_size > MAX_METADATA_BYTES:
            raise BackupError("Backup manifest exceeds the supported size.")
        with archive.open(info) as handle:
            manifest = json.loads(handle.read(MAX_METADATA_BYTES + 1))
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        if (
            manifest.get("format") != BACKUP_FORMAT
            or manifest.get("format_version") != BACKUP_VERSION
        ):
            raise BackupError("Backup format is unsupported.")
        declared = manifest["members"]
        artifacts = manifest["artifact_map"]
        if not isinstance(declared, dict) or not isinstance(artifacts, dict):
            raise ValueError("member maps must be objects")
        if set(declared) != set(names) - {"backup-manifest.json"}:
            raise BackupError("Backup member manifest is incomplete.")
        required = {
            "databases/corpus.sqlite3",
            "databases/state.sqlite3",
            "settings.json",
        }
        if not required <= set(declared):
            raise BackupError("Backup is missing required workspace files.")
        if (
            not isinstance(manifest["created_at"], str)
            or not isinstance(manifest["includes_property_cache"], bool)
            or manifest.get("secrets_included") is not False
        ):
            raise ValueError("invalid backup metadata")
        targets = set()
        for relative, member in artifacts.items():
            if (
                not _safe_relative(relative)
                or not relative.startswith("artifacts/")
                or not isinstance(member, str)
                or not member.startswith("artifacts/")
                or member not in declared
                or relative.casefold() in targets
            ):
                raise BackupError("Backup artifact map is unsafe or incomplete.")
            targets.add(relative.casefold())
        if len(set(artifacts.values())) != len(artifacts) or (
            set(declared) != required | set(artifacts.values())
        ):
            raise BackupError("Backup artifact map is incomplete.")
        # Reject file/directory collisions on case-insensitive filesystems too.
        for target in targets:
            if any(str(parent) in targets for parent in PurePosixPath(target).parents):
                raise BackupError("Backup artifact paths conflict.")
        for name, metadata in declared.items():
            if (
                not isinstance(metadata, dict)
                or type(metadata.get("size_bytes")) is not int
                or metadata["size_bytes"] != archive.getinfo(name).file_size
                or not isinstance(metadata.get("sha256"), str)
                or len(metadata["sha256"]) != 64
            ):
                raise BackupError(f"Backup member verification failed: {name}")
        if archive.getinfo("settings.json").file_size > MAX_METADATA_BYTES:
            raise BackupError("Backup settings exceed the supported size.")
        return manifest
    except (KeyError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise BackupError("Backup manifest is invalid.") from exc


def _copy_and_hash(source: BinaryIO, target: BinaryIO | None, *, limit: int) -> dict:
    digest = hashlib.sha256()
    size = 0
    while block := source.read(COPY_CHUNK_BYTES):
        size += len(block)
        if size > limit:
            raise BackupError("Backup member exceeds the supported size.")
        digest.update(block)
        if target is not None:
            target.write(block)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


def _read_backup(path: Path, *, destination: Path | None = None) -> dict:
    """Validate every byte, optionally streaming files into a private staging tree."""
    if not path.is_file() or path.stat().st_size > MAX_BACKUP_BYTES:
        raise BackupError("Backup is missing or exceeds the supported size.")
    try:
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive)
            targets = {
                "databases/corpus.sqlite3": "corpus.sqlite3",
                "databases/state.sqlite3": "state.sqlite3",
                "settings.json": "settings.json",
                **{
                    member: relative
                    for relative, member in manifest["artifact_map"].items()
                },
            }
            for name, expected in manifest["members"].items():
                with archive.open(name) as source:
                    if destination is None:
                        actual = _copy_and_hash(
                            source, None, limit=expected["size_bytes"]
                        )
                    else:
                        target = destination / targets[name]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as handle:
                            actual = _copy_and_hash(
                                source, handle, limit=expected["size_bytes"]
                            )
                if actual != expected:
                    raise BackupError(f"Backup member verification failed: {name}")
            return manifest
    except (OSError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("Backup archive is invalid or could not be read.") from exc


def _write_zip(destination: Path, members: dict[str, Path], metadata: dict) -> None:
    if len(members) + 1 > MAX_BACKUP_MEMBERS:
        raise BackupError("Backup contains too many members.")
    descriptor, name = tempfile.mkstemp(
        dir=destination.parent, prefix=".backup-", suffix=".tmp"
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        total = 0
        declared = {}
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for member, path in sorted(members.items()):
                with (
                    path.open("rb") as source,
                    archive.open(member, "w", force_zip64=True) as target,
                ):
                    declared[member] = _copy_and_hash(
                        source, target, limit=MAX_BACKUP_BYTES - total
                    )
                total += declared[member]["size_bytes"]
            manifest = _json_bytes({**metadata, "members": declared})
            if (
                len(manifest) > MAX_METADATA_BYTES
                or total + len(manifest) > MAX_BACKUP_BYTES
            ):
                raise BackupError("Backup exceeds the supported size.")
            archive.writestr("backup-manifest.json", manifest)
        if temporary.stat().st_size > MAX_BACKUP_BYTES:
            raise BackupError("Backup exceeds the supported size.")
        sync_file(temporary)
        os.replace(temporary, destination)
        sync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
