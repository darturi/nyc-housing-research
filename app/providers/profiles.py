from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit


class ProfileKind(StrEnum):
    ANSWER = "answer"
    EMBEDDING = "embedding"


@dataclass(frozen=True)
class ProviderProfile:
    id: str
    version: int
    kind: ProfileKind
    provider: str
    credential_slot: str | None
    model: str
    endpoint: str | None
    dimension: int | None
    input_usd_per_million: Decimal | None
    output_usd_per_million: Decimal | None
    max_input_tokens: int
    max_output_tokens: int | None
    token_estimator: str
    request_timeout_seconds: float
    max_attempts: int
    price_effective_date: str | None
    price_source: str | None
    stores_response: bool
    compatibility_verified: bool

    @property
    def paid(self) -> bool:
        return self.provider != "fake"

    @property
    def pricing_verified(self) -> bool:
        return self.input_usd_per_million is not None and (
            self.kind == ProfileKind.EMBEDDING
            or self.output_usd_per_million is not None
        )


PROFILES = {
    "fake-answer-small": ProviderProfile(
        id="fake-answer-small",
        version=1,
        kind=ProfileKind.ANSWER,
        provider="fake",
        credential_slot=None,
        model="fake-answer-small",
        endpoint=None,
        dimension=None,
        input_usd_per_million=Decimal("0"),
        output_usd_per_million=Decimal("0"),
        max_input_tokens=16_000,
        max_output_tokens=1_000,
        token_estimator="utf8_bytes_upper_bound_v1",
        request_timeout_seconds=60.0,
        max_attempts=1,
        price_effective_date=None,
        price_source=None,
        stores_response=False,
        compatibility_verified=True,
    ),
    "fake-small-16": ProviderProfile(
        id="fake-small-16",
        version=1,
        kind=ProfileKind.EMBEDDING,
        provider="fake",
        credential_slot=None,
        model="fake-small-16",
        endpoint=None,
        dimension=16,
        input_usd_per_million=Decimal("0"),
        output_usd_per_million=None,
        max_input_tokens=8_000,
        max_output_tokens=None,
        token_estimator="utf8_bytes_upper_bound_v1",
        request_timeout_seconds=60.0,
        max_attempts=1,
        price_effective_date=None,
        price_source=None,
        stores_response=False,
        compatibility_verified=True,
    ),
    "openai-answer-luna-v1": ProviderProfile(
        id="openai-answer-luna-v1",
        version=1,
        kind=ProfileKind.ANSWER,
        provider="openai",
        credential_slot="openai",
        model="gpt-5.6-luna",
        endpoint="https://api.openai.com/v1/responses",
        dimension=None,
        input_usd_per_million=Decimal("0.20"),
        output_usd_per_million=Decimal("1.20"),
        max_input_tokens=32_000,
        max_output_tokens=1_200,
        token_estimator="utf8_bytes_upper_bound_v1",
        request_timeout_seconds=60.0,
        max_attempts=1,
        price_effective_date="2026-09-14",
        price_source=("https://developers.openai.com/api/docs/models/gpt-5.6-luna"),
        stores_response=False,
        compatibility_verified=True,
    ),
    "openai-embedding-3-small-v1": ProviderProfile(
        id="openai-embedding-3-small-v1",
        version=1,
        kind=ProfileKind.EMBEDDING,
        provider="openai",
        credential_slot="openai",
        model="text-embedding-3-small",
        endpoint="https://api.openai.com/v1/embeddings",
        dimension=1536,
        input_usd_per_million=Decimal("0.02"),
        output_usd_per_million=None,
        max_input_tokens=8_191,
        max_output_tokens=None,
        token_estimator="utf8_bytes_upper_bound_v1",
        request_timeout_seconds=60.0,
        max_attempts=1,
        price_effective_date="2026-09-14",
        price_source=(
            "https://developers.openai.com/api/docs/models/text-embedding-3-small"
        ),
        stores_response=False,
        compatibility_verified=True,
    ),
}


def get_profile(profile_id: str, *, kind: ProfileKind | None = None) -> ProviderProfile:
    try:
        profile = PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown provider profile: {profile_id}") from exc
    if kind is not None and profile.kind != kind:
        raise ValueError(f"Profile {profile_id} is not a {kind.value} profile.")
    return profile


PROFILE_OVERRIDE_FIELDS = {
    "endpoint",
    "auth_slot",
    "input_usd_per_million",
    "output_usd_per_million",
    "price_effective_date",
    "price_source",
    "stores_response",
}
AUTH_SLOT_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


def get_configured_profile(
    settings: object,
    kind: ProfileKind,
    *,
    require_compatibility: bool = True,
) -> ProviderProfile:
    selection_id = str(
        getattr(
            settings,
            "answer_profile" if kind == ProfileKind.ANSWER else "embedding_profile",
        )
    )
    base = get_profile(selection_id, kind=kind)
    overrides = getattr(settings, "profile_overrides", {})
    raw = overrides.get(selection_id) if isinstance(overrides, dict) else None
    if raw is None:
        return base
    profile = apply_profile_override(base, raw)
    checks = getattr(settings, "profile_compatibility_checks", {})
    verified = isinstance(checks, dict) and checks.get(
        selection_id
    ) == profile_override_fingerprint(base, raw)
    if require_compatibility:
        return _replace_compatibility(profile, verified)
    return _replace_compatibility(profile, False)


def configured_profile_entries(settings: object) -> list[tuple[str, ProviderProfile]]:
    entries: list[tuple[str, ProviderProfile]] = []
    overrides = getattr(settings, "profile_overrides", {})
    for selection_id, base in PROFILES.items():
        raw = overrides.get(selection_id) if isinstance(overrides, dict) else None
        profile = apply_profile_override(base, raw) if raw is not None else base
        if raw is not None:
            checks = getattr(settings, "profile_compatibility_checks", {})
            verified = isinstance(checks, dict) and checks.get(
                selection_id
            ) == profile_override_fingerprint(base, raw)
            profile = _replace_compatibility(profile, verified)
        entries.append((selection_id, profile))
    return entries


def apply_profile_override(base: ProviderProfile, raw: object) -> ProviderProfile:
    if base.provider == "fake":
        raise ValueError("Synthetic profiles cannot use custom endpoints.")
    override = validate_profile_override(base, raw)
    identity = {
        "base_profile_id": base.id,
        "endpoint": override["endpoint"],
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    return ProviderProfile(
        id=f"{base.id}+local-{fingerprint}",
        version=base.version,
        kind=base.kind,
        provider="openai-compatible",
        credential_slot=str(override["auth_slot"]),
        model=base.model,
        endpoint=str(override["endpoint"]),
        dimension=base.dimension,
        input_usd_per_million=(
            Decimal(str(override["input_usd_per_million"]))
            if override["input_usd_per_million"] is not None
            else None
        ),
        output_usd_per_million=(
            Decimal(str(override["output_usd_per_million"]))
            if override["output_usd_per_million"] is not None
            else None
        ),
        max_input_tokens=base.max_input_tokens,
        max_output_tokens=base.max_output_tokens,
        token_estimator=base.token_estimator,
        request_timeout_seconds=base.request_timeout_seconds,
        max_attempts=base.max_attempts,
        price_effective_date=(
            str(override["price_effective_date"])
            if override["price_effective_date"] is not None
            else None
        ),
        price_source=(
            str(override["price_source"])
            if override["price_source"] is not None
            else None
        ),
        stores_response=bool(override["stores_response"]),
        compatibility_verified=False,
    )


def profile_override_fingerprint(base: ProviderProfile, raw: object) -> str:
    override = validate_profile_override(base, raw)
    payload = {"base_profile_id": base.id, **override}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def validate_profile_override(base: ProviderProfile, raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != PROFILE_OVERRIDE_FIELDS:
        raise ValueError(
            "A custom profile requires endpoint, auth_slot, complete or null "
            "pricing fields, and stores_response metadata."
        )
    endpoint = str(raw["endpoint"]).strip()
    _validate_endpoint(endpoint)
    auth_slot = validate_auth_slot(str(raw["auth_slot"]))
    input_value = raw["input_usd_per_million"]
    output_value = raw["output_usd_per_million"]
    price_date_value = raw["price_effective_date"]
    price_source_value = raw["price_source"]
    pricing_unknown = (
        input_value is None
        and output_value is None
        and price_date_value is None
        and price_source_value is None
    )
    if pricing_unknown:
        input_price: Decimal | None = None
        output_price: Decimal | None = None
        price_date: str | None = None
        price_source: str | None = None
    elif any(
        value is None
        for value in (
            input_value,
            price_date_value,
            price_source_value,
            output_value if base.kind == ProfileKind.ANSWER else input_value,
        )
    ):
        raise ValueError(
            "Custom profile pricing must be either fully specified or explicitly "
            "unknown."
        )
    else:
        input_price = _price(input_value, "input")
        output_price = (
            _price(output_value, "output") if base.kind == ProfileKind.ANSWER else None
        )
        price_date = str(price_date_value).strip()
        try:
            date.fromisoformat(price_date)
        except ValueError as exc:
            raise ValueError(
                "Custom profile price_effective_date must be YYYY-MM-DD."
            ) from exc
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", price_date):
            raise ValueError("Custom profile price_effective_date must be YYYY-MM-DD.")
        price_source = str(price_source_value).strip()
        parsed_source = urlsplit(price_source)
        if parsed_source.scheme != "https" or not parsed_source.hostname:
            raise ValueError("Custom profile price_source must be an HTTPS URL.")
    if base.kind == ProfileKind.EMBEDDING and output_value is not None:
        raise ValueError("Embedding profile output price must be null.")
    stores_response = raw["stores_response"]
    if not isinstance(stores_response, bool):
        raise ValueError("Custom profile stores_response must be true or false.")
    return {
        "endpoint": endpoint,
        "auth_slot": auth_slot,
        "input_usd_per_million": (
            str(input_price) if input_price is not None else None
        ),
        "output_usd_per_million": (
            str(output_price) if output_price is not None else None
        ),
        "price_effective_date": price_date,
        "price_source": price_source,
        "stores_response": stores_response,
    }


def validate_auth_slot(value: str) -> str:
    normalized = value.strip().lower()
    if not AUTH_SLOT_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Credential slot must start with a letter and contain only lowercase "
            "letters, digits, or hyphens (maximum 64 characters)."
        )
    return normalized


def _price(value: object, label: str) -> Decimal:
    try:
        price = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Custom profile {label} price must be a decimal.") from exc
    if not price.is_finite() or price < 0:
        raise ValueError(f"Custom profile {label} price must be nonnegative.")
    return price


def _validate_endpoint(value: str) -> None:
    parsed = urlsplit(value)
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Custom endpoint must be an absolute URL without credentials, query, "
            "or fragment."
        )
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _loopback(parsed.hostname):
        return
    raise ValueError("Custom endpoints require HTTPS, except for loopback HTTP.")


def _loopback(hostname: str) -> bool:
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _replace_compatibility(profile: ProviderProfile, verified: bool) -> ProviderProfile:
    from dataclasses import replace

    return replace(profile, compatibility_verified=verified)
