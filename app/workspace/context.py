from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.workspace.network import NetworkPolicy
from app.workspace.paths import WorkspacePaths, resolve_workspace_paths
from app.workspace.settings import (
    LocalSettings,
    legacy_environment_names,
    load_local_settings,
    save_local_settings,
)


@dataclass(frozen=True)
class WorkspaceContext:
    paths: WorkspacePaths
    settings: LocalSettings
    network: NetworkPolicy
    detected_legacy_environment: tuple[str, ...] = ()

    @classmethod
    def from_options(
        cls,
        data_dir: str | Path | None = None,
        config_file: str | Path | None = None,
        *,
        environment: Mapping[str, str] | None = None,
        initialize: bool = False,
    ) -> WorkspaceContext:
        paths = resolve_workspace_paths(
            data_dir=data_dir,
            config_file=config_file,
            environment=environment,
        )
        settings = load_local_settings(paths, environment=environment)
        context = cls(
            paths=paths,
            settings=settings,
            network=NetworkPolicy(offline=settings.offline),
            detected_legacy_environment=tuple(legacy_environment_names(environment)),
        )
        if initialize:
            context.initialize()
        return context

    @property
    def initialized(self) -> bool:
        return self.paths.config_file.is_file() and self.paths.root.is_dir()

    def initialize(self) -> None:
        self.paths.create()
        save_local_settings(self.paths, self.settings)
