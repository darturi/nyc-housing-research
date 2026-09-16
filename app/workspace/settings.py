from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.providers.profiles import (
    ProfileKind,
    apply_profile_override,
    get_configured_profile,
    get_profile,
)
from app.workspace.paths import WorkspacePaths

SETTINGS_SCHEMA_VERSION = 1
SECRET_KEY_FRAGMENTS = ("api_key", "password", "secret", "token", "credential")


class LocalSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class LocalSettings:
    schema_version: int = SETTINGS_SCHEMA_VERSION
    workspace_id: str = ""
    app_name: str = "NYC Housing Research"
    host: str = "127.0.0.1"
    port: int = 8000
    open_browser: bool = True
    offline: bool = False
    monthly_budget_usd: str = "15.00"
    per_operation_budget_usd: str = "2.00"
    max_concurrent_paid_requests: int = 2
    answer_deadline_seconds: int = 60
    budget_timezone: str = "America/New_York"
    property_cache_max_mb: int = 500
    property_cache_retention_days: int = 30
    operational_retention_days: int = 30
    usage_retention_months: int = 12
    history_enabled: bool = False
    answer_profile: str = "fake-answer-small"
    embedding_profile: str = "fake-small-16"
    profile_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    profile_compatibility_checks: dict[str, str] = field(default_factory=dict)

    def validate(self) -> LocalSettings:
        if self.schema_version != SETTINGS_SCHEMA_VERSION:
            raise LocalSettingsError(
                f"Unsupported settings schema version {self.schema_version}."
            )
        if not self.workspace_id:
            raise LocalSettingsError("workspace_id is required.")
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise LocalSettingsError("Native local mode only supports loopback hosts.")
        if not 1 <= self.port <= 65535:
            raise LocalSettingsError("port must be between 1 and 65535.")
        try:
            budget = Decimal(self.monthly_budget_usd)
            operation_budget = Decimal(self.per_operation_budget_usd)
        except InvalidOperation as exc:
            raise LocalSettingsError("Budget settings must be decimal values.") from exc
        if (
            not budget.is_finite()
            or budget < 0
            or not operation_budget.is_finite()
            or operation_budget < 0
        ):
            raise LocalSettingsError("Budget settings must be finite and nonnegative.")
        if not 1 <= self.max_concurrent_paid_requests <= 2:
            raise LocalSettingsError(
                "max_concurrent_paid_requests must be either 1 or 2."
            )
        if not 5 <= self.answer_deadline_seconds <= 300:
            raise LocalSettingsError(
                "answer_deadline_seconds must be between 5 and 300."
            )
        try:
            ZoneInfo(self.budget_timezone)
        except ZoneInfoNotFoundError as exc:
            raise LocalSettingsError("budget_timezone is not recognized.") from exc
        if not 10 <= self.property_cache_max_mb <= 10_000:
            raise LocalSettingsError(
                "property_cache_max_mb must be between 10 and 10,000."
            )
        if not 1 <= self.property_cache_retention_days <= 365:
            raise LocalSettingsError(
                "property_cache_retention_days must be between 1 and 365."
            )
        if not 1 <= self.operational_retention_days <= 3_650:
            raise LocalSettingsError(
                "operational_retention_days must be between 1 and 3,650."
            )
        if not 1 <= self.usage_retention_months <= 120:
            raise LocalSettingsError(
                "usage_retention_months must be between 1 and 120."
            )
        try:
            get_profile(self.answer_profile, kind=ProfileKind.ANSWER)
            get_profile(self.embedding_profile, kind=ProfileKind.EMBEDDING)
            if not isinstance(self.profile_overrides, dict):
                raise ValueError("profile_overrides must be an object.")
            for profile_id, override in self.profile_overrides.items():
                apply_profile_override(get_profile(profile_id), override)
            if not isinstance(self.profile_compatibility_checks, dict) or any(
                profile_id not in self.profile_overrides
                or not isinstance(fingerprint, str)
                for profile_id, fingerprint in self.profile_compatibility_checks.items()
            ):
                raise ValueError(
                    "profile_compatibility_checks contains invalid or unknown entries."
                )
            get_configured_profile(self, ProfileKind.ANSWER)
            get_configured_profile(self, ProfileKind.EMBEDDING)
        except ValueError as exc:
            raise LocalSettingsError(str(exc)) from exc
        return self

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_local_settings(
    paths: WorkspacePaths,
    *,
    environment: Mapping[str, str] | None = None,
) -> LocalSettings:
    environment = os.environ if environment is None else environment
    if paths.config_file.exists():
        try:
            payload = json.loads(paths.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSettingsError(
                f"Could not read local settings at {paths.config_file}."
            ) from exc
        if not isinstance(payload, dict):
            raise LocalSettingsError("Local settings must be a JSON object.")
        _reject_secrets(payload)
        allowed = {field.name for field in fields(LocalSettings)}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise LocalSettingsError("Unknown local setting(s): " + ", ".join(unknown))
        settings = LocalSettings(**payload)
    else:
        settings = LocalSettings(workspace_id=_workspace_id(paths.root))

    overrides: dict[str, Any] = {}
    if "NYC_HOUSING_OFFLINE" in environment:
        overrides["offline"] = _parse_bool(
            "NYC_HOUSING_OFFLINE", environment["NYC_HOUSING_OFFLINE"]
        )
    if "NYC_HOUSING_PORT" in environment:
        try:
            overrides["port"] = int(environment["NYC_HOUSING_PORT"])
        except ValueError as exc:
            raise LocalSettingsError("NYC_HOUSING_PORT must be an integer.") from exc
    if overrides:
        settings = LocalSettings(**(settings.public_dict() | overrides))
    return settings.validate()


def save_local_settings(paths: WorkspacePaths, settings: LocalSettings) -> None:
    settings.validate()
    payload = settings.public_dict()
    _reject_secrets(payload)
    paths.create()
    descriptor, temporary_name = tempfile.mkstemp(
        dir=paths.config_file.parent,
        prefix=f".{paths.config_file.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary_path.chmod(0o600)
        os.replace(temporary_path, paths.config_file)
    finally:
        temporary_path.unlink(missing_ok=True)


def legacy_environment_names(environment: Mapping[str, str] | None = None) -> list[str]:
    environment = os.environ if environment is None else environment
    names = {
        "DATABASE_URL",
        "ARTIFACT_S3_BUCKET",
        "ARTIFACT_S3_ACCESS_KEY_ID",
        "ARTIFACT_S3_SECRET_ACCESS_KEY",
        "EMBEDDING_API_KEY",
        "ANSWER_LLM_API_KEY",
    }
    return sorted(name for name in names if environment.get(name))


def _workspace_id(root: Path) -> str:
    import hashlib

    return "local-" + hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:20]


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise LocalSettingsError(f"{name} must be true or false.")


def _reject_secrets(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        normalized = key.lower()
        if any(fragment in normalized for fragment in SECRET_KEY_FRAGMENTS):
            if value not in (None, "", False):
                raise LocalSettingsError(
                    f"Secret-like field {key!r} cannot be stored in local settings."
                )
        if isinstance(value, dict):
            _reject_secrets(value)
