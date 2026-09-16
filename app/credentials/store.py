from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.providers.profiles import validate_auth_slot
from app.workspace.context import WorkspaceContext

ENVIRONMENT_NAMES = {
    "openai": "NYC_HOUSING_OPENAI_API_KEY",
    "socrata": "NYC_HOUSING_SOCRATA_APP_TOKEN",
}


class CredentialStoreError(RuntimeError):
    pass


class WritableCredentialStore(Protocol):
    name: str

    def available(self) -> bool: ...

    def get(self, provider: str) -> str | None: ...

    def set(self, provider: str, value: str) -> None: ...

    def delete(self, provider: str) -> None: ...


@dataclass(frozen=True)
class CredentialPresence:
    provider: str
    present: bool
    source: str | None


class EnvironmentCredentialStore:
    name = "environment"

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def get(self, provider: str) -> str | None:
        name = environment_name(provider)
        value = self._environment.get(name, "").strip() if name else ""
        return value or None


class KeyringCredentialStore:
    name = "keyring"

    def __init__(self, workspace_id: str) -> None:
        self._service = f"nyc-housing-rag:{workspace_id}"

    def available(self) -> bool:
        try:
            import keyring

            backend = keyring.get_keyring()
            return getattr(backend, "priority", 0) > 0
        except Exception:
            return False

    def get(self, provider: str) -> str | None:
        keyring = self._module()
        try:
            return keyring.get_password(self._service, provider)
        except Exception as exc:
            raise CredentialStoreError(
                "The OS credential store could not be read."
            ) from exc

    def set(self, provider: str, value: str) -> None:
        keyring = self._module()
        try:
            keyring.set_password(self._service, provider, value)
        except Exception as exc:
            raise CredentialStoreError(
                "The OS credential store rejected the credential."
            ) from exc

    def delete(self, provider: str) -> None:
        keyring = self._module()
        try:
            keyring.delete_password(self._service, provider)
        except keyring.errors.PasswordDeleteError:
            return
        except Exception as exc:
            raise CredentialStoreError(
                "The OS credential could not be removed."
            ) from exc

    def _module(self):
        try:
            import keyring
        except ImportError as exc:
            raise CredentialStoreError(
                "OS credential support is not installed; install the credentials extra "
                "or explicitly choose the protected secret-file fallback."
            ) from exc
        if not self.available():
            raise CredentialStoreError(
                "No usable OS credential-store backend is available."
            )
        return keyring


class SecretFileCredentialStore:
    name = "secret_file"

    def __init__(self, path: Path) -> None:
        self._path = path

    def available(self) -> bool:
        return True

    def get(self, provider: str) -> str | None:
        return self._read().get(provider)

    def set(self, provider: str, value: str) -> None:
        payload = self._read()
        payload[provider] = value
        self._write(payload)

    def delete(self, provider: str) -> None:
        payload = self._read()
        payload.pop(provider, None)
        self._write(payload)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        if os.name != "nt" and self._path.stat().st_mode & 0o077:
            raise CredentialStoreError(
                "Secret file permissions are too broad; require owner-only access."
            )
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialStoreError("The secret file could not be read.") from exc
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in payload.items()
        ):
            raise CredentialStoreError("The secret file format is invalid.")
        return payload

    def _write(self, payload: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=self._path.parent, prefix=".credentials-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            if os.name != "nt":
                temporary.chmod(0o600)
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


class CredentialResolver:
    def __init__(
        self,
        context: WorkspaceContext,
        *,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.environment = EnvironmentCredentialStore(environment)
        self.keyring = KeyringCredentialStore(context.settings.workspace_id)
        self.secret_file = SecretFileCredentialStore(
            context.paths.root / "credentials.json"
        )

    def resolve(self, provider: str) -> tuple[str | None, str | None]:
        value = self.environment.get(provider)
        if value:
            return value, self.environment.name
        if self.keyring.available():
            value = self.keyring.get(provider)
            if value:
                return value, self.keyring.name
        value = self.secret_file.get(provider)
        if value:
            return value, self.secret_file.name
        return None, None

    def presence(self, provider: str) -> CredentialPresence:
        value, source = self.resolve(provider)
        return CredentialPresence(provider=provider, present=bool(value), source=source)

    def writable(self, backend: str) -> WritableCredentialStore:
        if backend == "keyring":
            if not self.keyring.available():
                raise CredentialStoreError(
                    "No OS credential store is available. Install the "
                    "credentials extra or explicitly use --storage file."
                )
            return self.keyring
        if backend == "file":
            return self.secret_file
        raise CredentialStoreError(f"Unsupported credential storage: {backend}")


def validate_credential(provider: str, value: str) -> str:
    value = value.strip()
    try:
        validate_auth_slot(provider)
    except ValueError as exc:
        raise CredentialStoreError(str(exc)) from exc
    if (
        len(value) < 12
        or len(value) > 512
        or any(character.isspace() for character in value)
    ):
        raise CredentialStoreError("Credential format is invalid.")
    return value


def environment_name(slot: str) -> str:
    try:
        slot = validate_auth_slot(slot)
    except ValueError:
        return ""
    return ENVIRONMENT_NAMES.get(
        slot,
        "NYC_HOUSING_AUTH_" + slot.replace("-", "_").upper(),
    )


def configured_credential_slots(settings: object) -> tuple[str, ...]:
    slots = {"openai", "socrata"}
    overrides = getattr(settings, "profile_overrides", {})
    if isinstance(overrides, dict):
        for override in overrides.values():
            if isinstance(override, dict) and isinstance(
                override.get("auth_slot"), str
            ):
                slots.add(validate_auth_slot(override["auth_slot"]))
    return tuple(sorted(slots))
