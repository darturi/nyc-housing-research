from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

APPLICATION_DIRECTORY = "nyc-housing-rag"


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    config_file: Path

    @property
    def corpus_database(self) -> Path:
        return self.root / "corpus.sqlite3"

    @property
    def state_database(self) -> Path:
        return self.root / "state.sqlite3"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def backups(self) -> Path:
        return self.root / "backups"

    @property
    def extensions(self) -> Path:
        return self.root / "extensions"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def manifest(self) -> Path:
        return self.root / "workspace.json"

    def create(self) -> None:
        for path in (
            self.root,
            self.artifacts,
            self.exports,
            self.backups,
            self.extensions,
            self.logs,
            self.config_file.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)
            _restrict_directory(path)


def resolve_workspace_paths(
    data_dir: str | os.PathLike[str] | None = None,
    config_file: str | os.PathLike[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> WorkspacePaths:
    environment = os.environ if environment is None else environment
    platform = sys.platform if platform is None else platform
    home = Path.home() if home is None else home

    explicit_data_dir = data_dir or environment.get("NYC_HOUSING_DATA_DIR")
    if explicit_data_dir:
        root = _absolute_path(explicit_data_dir)
    else:
        root = _default_data_root(platform, home, environment)

    explicit_config = config_file or environment.get("NYC_HOUSING_CONFIG")
    if explicit_config:
        resolved_config = _absolute_path(explicit_config)
    elif explicit_data_dir:
        resolved_config = root / "settings.json"
    else:
        resolved_config = _default_config_root(platform, home, environment) / (
            "settings.json"
        )

    return WorkspacePaths(root=root, config_file=resolved_config)


def _default_data_root(
    platform: str,
    home: Path,
    environment: Mapping[str, str],
) -> Path:
    if platform == "darwin":
        root = home / "Library" / "Application Support" / APPLICATION_DIRECTORY
        return root.resolve()
    if platform.startswith("win"):
        base = environment.get("LOCALAPPDATA")
        root = Path(base) if base else home / "AppData" / "Local"
        return (root / APPLICATION_DIRECTORY).resolve()
    base = environment.get("XDG_DATA_HOME")
    root = Path(base) if base else home / ".local" / "share"
    return (root / APPLICATION_DIRECTORY).resolve()


def _default_config_root(
    platform: str,
    home: Path,
    environment: Mapping[str, str],
) -> Path:
    if platform == "darwin":
        root = home / "Library" / "Application Support" / APPLICATION_DIRECTORY
        return root.resolve()
    if platform.startswith("win"):
        base = environment.get("APPDATA")
        root = Path(base) if base else home / "AppData" / "Roaming"
        return (root / APPLICATION_DIRECTORY).resolve()
    base = environment.get("XDG_CONFIG_HOME")
    root = Path(base) if base else home / ".config"
    return (root / APPLICATION_DIRECTORY).resolve()


def _absolute_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _restrict_directory(path: Path) -> None:
    if os.name == "nt":
        return
    path.chmod(0o700)
