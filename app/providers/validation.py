from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.credentials.store import CredentialResolver
from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import (
    ProfileKind,
    get_configured_profile,
    get_profile,
    profile_override_fingerprint,
)
from app.storage.database import LocalStorage
from app.usage.ledger import SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext
from app.workspace.settings import save_local_settings

VALIDATION_TEXT = "credential validation"
VALIDATION_PROFILE = {"openai": "openai-embedding-3-small-v1"}


@dataclass(frozen=True)
class CredentialValidationEstimate:
    provider: str
    profile_id: str
    model: str
    estimated_input_tokens: int
    estimated_cost_usd: Decimal
    price_effective_date: str | None
    price_source: str | None


@dataclass(frozen=True)
class CredentialValidationResult:
    operation_id: str
    provider: str
    status: str
    profile_id: str
    model: str
    input_tokens: int
    cost_usd: Decimal


@dataclass(frozen=True)
class ProfileCompatibilityEstimate:
    kind: str
    selection_id: str
    effective_profile_id: str
    provider: str
    model: str
    endpoint: str | None
    credential_slot: str | None
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: Decimal | None
    cost_known: bool
    stores_response: bool
    price_effective_date: str | None
    price_source: str | None


@dataclass(frozen=True)
class ProfileCompatibilityResult:
    operation_id: str
    kind: str
    selection_id: str
    effective_profile_id: str
    provider: str
    model: str
    endpoint: str | None
    credential_slot: str | None
    status: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal | None
    cost_known: bool
    stores_response: bool


def credential_validation_estimate(provider: str) -> CredentialValidationEstimate:
    profile_id = VALIDATION_PROFILE.get(provider)
    if profile_id is None:
        raise ValueError(
            "Direct validation is unavailable for this provider; use doctor --online."
        )
    profile = get_profile(profile_id, kind=ProfileKind.EMBEDDING)
    tokens = max(1, (len(VALIDATION_TEXT.encode("utf-8")) + 3) // 4)
    if profile.input_usd_per_million is None:
        raise ValueError("The validation profile has no verified price.")
    cost = (
        Decimal(tokens) * profile.input_usd_per_million / Decimal(1_000_000)
    ).quantize(Decimal("0.00000001"))
    return CredentialValidationEstimate(
        provider=provider,
        profile_id=profile.id,
        model=profile.model,
        estimated_input_tokens=tokens,
        estimated_cost_usd=cost,
        price_effective_date=profile.price_effective_date,
        price_source=profile.price_source,
    )


def run_credential_validation(
    context: WorkspaceContext,
    storage: LocalStorage,
    provider: str,
    *,
    approve_cost: bool,
    max_cost_usd: Decimal | None,
    gateway: ProviderGateway | None = None,
    credentials: CredentialResolver | None = None,
) -> CredentialValidationResult:
    estimate = credential_validation_estimate(provider)
    if not approve_cost:
        raise ValueError("Credential validation requires explicit cost approval.")
    if max_cost_usd is None:
        raise ValueError("Credential validation requires an explicit cost ceiling.")
    if not max_cost_usd.is_finite() or max_cost_usd < 0:
        raise ValueError("Credential validation cost ceiling must be nonnegative.")
    if estimate.estimated_cost_usd > max_cost_usd:
        raise SpendDenied("Credential validation estimate exceeds the cost ceiling.")
    profile = get_profile(estimate.profile_id, kind=ProfileKind.EMBEDDING)
    resolver = credentials or CredentialResolver(context)
    credential, _source = resolver.resolve(provider)
    if not credential:
        raise ValueError("The provider credential is missing.")
    active_gateway = gateway or ProviderGateway(context, UsageLedger(storage))
    try:
        response = active_gateway.embeddings(
            inputs=[VALIDATION_TEXT],
            profile=profile,
            credential=credential,
        )
    finally:
        if gateway is None:
            active_gateway.close()
    return CredentialValidationResult(
        operation_id=response.operation_id,
        provider=provider,
        status="valid",
        profile_id=profile.id,
        model=profile.model,
        input_tokens=response.input_tokens,
        cost_usd=response.cost_usd,
    )


def profile_compatibility_estimate(
    context: WorkspaceContext, kind: ProfileKind
) -> ProfileCompatibilityEstimate:
    profile = get_configured_profile(
        context.settings, kind, require_compatibility=False
    )
    selection_id = _selection_id(context, kind)
    text = _compatibility_text(kind)
    input_tokens = max(1, (len(text.encode("utf-8")) + 3) // 4)
    output_tokens = profile.max_output_tokens or 0
    input_rate = profile.input_usd_per_million
    output_rate = profile.output_usd_per_million or Decimal("0")
    cost = (
        (
            Decimal(input_tokens) * input_rate
            + Decimal(output_tokens) * output_rate
        )
        / Decimal(1_000_000)
    ).quantize(Decimal("0.00000001")) if input_rate is not None else None
    return ProfileCompatibilityEstimate(
        kind=kind.value,
        selection_id=selection_id,
        effective_profile_id=profile.id,
        provider=profile.provider,
        model=profile.model,
        endpoint=profile.endpoint,
        credential_slot=profile.credential_slot,
        estimated_input_tokens=input_tokens,
        estimated_output_tokens=output_tokens,
        estimated_cost_usd=cost,
        cost_known=cost is not None,
        stores_response=profile.stores_response,
        price_effective_date=profile.price_effective_date,
        price_source=profile.price_source,
    )


def run_profile_compatibility_check(
    context: WorkspaceContext,
    storage: LocalStorage,
    kind: ProfileKind,
    *,
    approve_cost: bool,
    max_cost_usd: Decimal | None,
    allow_unknown_cost: bool = False,
    gateway: ProviderGateway | None = None,
    credentials: CredentialResolver | None = None,
) -> ProfileCompatibilityResult:
    estimate = profile_compatibility_estimate(context, kind)
    if estimate.cost_known:
        if not approve_cost:
            raise ValueError(
                "Profile compatibility checks require explicit cost approval."
            )
        if max_cost_usd is None:
            raise ValueError("Profile compatibility checks require a cost ceiling.")
        if not max_cost_usd.is_finite() or max_cost_usd < 0:
            raise ValueError(
                "Profile compatibility cost ceiling must be nonnegative."
            )
        assert estimate.estimated_cost_usd is not None
        if estimate.estimated_cost_usd > max_cost_usd:
            raise SpendDenied(
                "Profile compatibility estimate exceeds the cost ceiling."
            )
    else:
        if not allow_unknown_cost:
            raise ValueError(
                "This profile has unknown pricing. The one-off compatibility "
                "request requires --allow-unknown-cost and is outside USD caps."
            )
        if max_cost_usd is not None:
            raise ValueError(
                "A USD ceiling cannot cover a request whose provider price is "
                "unknown."
            )
    raw_profile = get_configured_profile(
        context.settings, kind, require_compatibility=False
    )
    profile = replace(raw_profile, compatibility_verified=True)
    resolver = credentials or CredentialResolver(context)
    slot = profile.credential_slot or profile.provider
    credential, _source = resolver.resolve(slot)
    if profile.paid and not credential:
        raise ValueError(f"Credential slot {slot!r} is missing.")
    active_gateway = gateway or ProviderGateway(context, UsageLedger(storage))
    try:
        if kind == ProfileKind.EMBEDDING:
            response = active_gateway.embeddings(
                inputs=[_compatibility_text(kind)],
                profile=profile,
                credential=credential,
                allow_unknown_cost=allow_unknown_cost,
            )
            operation_id = response.operation_id
            input_tokens = response.input_tokens
            output_tokens = 0
            cost = response.cost_usd
        else:
            response = active_gateway.answer(
                prompt=_compatibility_text(kind),
                profile=profile,
                credential=credential,
                allow_unknown_cost=allow_unknown_cost,
            )
            if "[E1]" not in response.text:
                raise ProviderExecutionError(
                    "The answer endpoint returned a valid shape but did not follow "
                    "the compatibility marker instruction."
                )
            operation_id = response.operation_id
            input_tokens = response.input_tokens
            output_tokens = response.output_tokens
            cost = response.cost_usd
    finally:
        if gateway is None:
            active_gateway.close()
    selection_id = _selection_id(context, kind)
    if selection_id in context.settings.profile_overrides:
        checks = dict(context.settings.profile_compatibility_checks)
        checks[selection_id] = profile_override_fingerprint(
            get_profile(selection_id, kind=kind),
            context.settings.profile_overrides[selection_id],
        )
        save_local_settings(
            context.paths,
            replace(context.settings, profile_compatibility_checks=checks),
        )
    return ProfileCompatibilityResult(
        operation_id=operation_id,
        kind=kind.value,
        selection_id=selection_id,
        effective_profile_id=profile.id,
        provider=profile.provider,
        model=profile.model,
        endpoint=profile.endpoint,
        credential_slot=profile.credential_slot,
        status="compatible",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        cost_known=cost is not None,
        stores_response=profile.stores_response,
    )


def _selection_id(context: WorkspaceContext, kind: ProfileKind) -> str:
    return (
        context.settings.answer_profile
        if kind == ProfileKind.ANSWER
        else context.settings.embedding_profile
    )


def _compatibility_text(kind: ProfileKind) -> str:
    if kind == ProfileKind.EMBEDDING:
        return "NYC Housing provider compatibility check"
    return (
        "Return a short plain-text compatibility response containing the marker "
        "[E1]. Do not provide legal information."
    )
