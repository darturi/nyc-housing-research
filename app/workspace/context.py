from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
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
    network: NetworkPolicy | LiveNetworkPolicy
    detected_legacy_environment: tuple[str, ...] = ()
    policy: WorkspacePolicy | None = field(default=None, compare=False, repr=False)

    def current(self) -> WorkspaceContext:
        return self.policy.context if self.policy is not None else self

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
            network=network_policy_for_settings(settings),
            detected_legacy_environment=tuple(legacy_environment_names(environment)),
        )
        if initialize:
            context.initialize()
        return context

    @property
    def initialized(self) -> bool:
        return self.paths.config_file.is_file() and self.paths.root.is_dir()

    def initialize(self) -> None:
        from app.storage.database import assert_no_pending_migration

        assert_no_pending_migration(self.paths)
        self.paths.create()
        save_local_settings(self.paths, self.settings)


def network_policy_for_settings(settings: LocalSettings) -> NetworkPolicy:
    endpoint = settings.local_runtime_endpoint
    enabled = bool(settings.local_runtime_enabled and endpoint)
    return NetworkPolicy(
        offline=settings.offline,
        allow_loopback_services=enabled,
        allowed_loopback_urls=(str(endpoint),) if enabled else (),
    )


class WorkspacePolicy:
    """Publish an immutable policy snapshot shared by API and background work."""

    def __init__(self, context: WorkspaceContext) -> None:
        self.context = context

    def update(self, context: WorkspaceContext) -> None:
        self.context = context


class LiveNetworkPolicy:
    """Already-created connectors must honor a subsequent offline switch."""

    def __init__(self, policy: WorkspacePolicy) -> None:
        self.policy = policy

    @property
    def offline(self) -> bool:
        return self.policy.context.settings.offline

    @property
    def allow_loopback_services(self) -> bool:
        return network_policy_for_settings(
            self.policy.context.settings
        ).allow_loopback_services

    @property
    def allowed_loopback_urls(self) -> tuple[str, ...]:
        return network_policy_for_settings(
            self.policy.context.settings
        ).allowed_loopback_urls

    def assert_url_allowed(self, url: str, *, purpose: str) -> None:
        network_policy_for_settings(self.policy.context.settings).assert_url_allowed(
            url, purpose=purpose
        )
