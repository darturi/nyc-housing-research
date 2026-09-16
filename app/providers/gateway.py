from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import httpx

from app.jobs.runtime import CancellationSignal, Deadline
from app.providers.profiles import ProfileKind, ProviderProfile
from app.usage.ledger import UsageLedger, UsageReservation
from app.workspace.context import WorkspaceContext


class ProviderExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderAnswer:
    text: str
    input_tokens: int
    output_tokens: int
    operation_id: str
    attempt_id: str
    cost_usd: Decimal | None
    cost_known: bool


@dataclass(frozen=True)
class ProviderEmbeddings:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int
    operation_id: str
    attempt_id: str
    cost_usd: Decimal | None
    cost_known: bool


class ProviderGateway:
    def __init__(
        self,
        context: WorkspaceContext,
        ledger: UsageLedger,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._context = context
        self._ledger = ledger
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def answer(
        self,
        *,
        prompt: str,
        profile: ProviderProfile,
        credential: str | None,
        operation_id: str | None = None,
        deadline: Deadline | None = None,
        cancellation: CancellationSignal | None = None,
        allow_unknown_cost: bool = False,
    ) -> ProviderAnswer:
        if profile.kind != ProfileKind.ANSWER:
            raise ProviderExecutionError("Selected profile is not an answer profile.")
        if not profile.compatibility_verified:
            raise ProviderExecutionError(
                "The custom answer endpoint has not passed its explicit "
                "compatibility check."
            )
        operation_id = operation_id or str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        _check(deadline, cancellation)
        if profile.provider != "fake":
            if not credential:
                raise ProviderExecutionError(
                    "The selected provider credential is missing."
                )
            assert profile.endpoint is not None
            self._context.network.assert_url_allowed(
                profile.endpoint, purpose="answer provider"
            )
        input_tokens = _estimate_tokens(prompt)
        output_tokens = profile.max_output_tokens or 0
        projected = _cost(profile, input_tokens, output_tokens)
        snapshot = _price_snapshot(profile)
        reservation = self._reserve(
            operation_id,
            attempt_id,
            profile,
            projected,
            snapshot,
            allow_unknown_cost=allow_unknown_cost,
        )
        if profile.provider == "fake":
            marker = "[P1]" if "Property evidence:" in prompt else "[E1]"
            text = (
                "Synthetic provider output for interface testing only; this is not "
                f"a substantive legal answer {marker}."
            )
            actual_output = _estimate_tokens(text)
            actual = _cost(profile, input_tokens, actual_output)
            self._settle(
                reservation,
                profile,
                actual,
                input_tokens,
                actual_output,
                snapshot,
            )
            return ProviderAnswer(
                text,
                input_tokens,
                actual_output,
                operation_id,
                attempt_id,
                actual,
                actual is not None,
            )
        assert profile.endpoint is not None
        try:
            response = self._client.post(
                profile.endpoint,
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "model": profile.model,
                    "input": prompt,
                    "max_output_tokens": profile.max_output_tokens,
                    "store": False,
                },
                timeout=_timeout(deadline, profile),
            )
        except httpx.HTTPError as exc:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The answer provider did not return a usable response."
            ) from exc
        _check(deadline, cancellation, reservation, self, profile, snapshot)
        if response.status_code >= 500:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The answer provider failed after request submission."
            )
        if response.status_code >= 400:
            self._settle(reservation, profile, Decimal("0"), 0, 0, snapshot)
            raise ProviderExecutionError(
                f"The answer provider rejected the request ({response.status_code})."
            )
        try:
            payload = response.json()
            if payload.get("status") == "incomplete":
                raise ValueError("incomplete provider response")
            text = _response_text(payload)
            usage = payload.get("usage") or {}
            actual_input = int(usage.get("input_tokens", input_tokens))
            actual_output = int(usage.get("output_tokens", _estimate_tokens(text)))
        except (TypeError, ValueError, KeyError) as exc:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The answer provider response was malformed."
            ) from exc
        actual = _cost(profile, actual_input, actual_output)
        self._settle(
            reservation,
            profile,
            actual,
            actual_input,
            actual_output,
            snapshot,
        )
        return ProviderAnswer(
            text,
            actual_input,
            actual_output,
            operation_id,
            attempt_id,
            actual,
            actual is not None,
        )

    def embeddings(
        self,
        *,
        inputs: list[str],
        profile: ProviderProfile,
        credential: str | None,
        operation_id: str | None = None,
        deadline: Deadline | None = None,
        cancellation: CancellationSignal | None = None,
        allow_unknown_cost: bool = False,
    ) -> ProviderEmbeddings:
        if profile.kind != ProfileKind.EMBEDDING or profile.dimension is None:
            raise ProviderExecutionError(
                "Selected profile is not an embedding profile."
            )
        if not profile.compatibility_verified:
            raise ProviderExecutionError(
                "The custom embedding endpoint has not passed its explicit "
                "compatibility check."
            )
        if not inputs:
            raise ProviderExecutionError("At least one embedding input is required.")
        operation_id = operation_id or str(uuid.uuid4())
        attempt_id = str(uuid.uuid4())
        _check(deadline, cancellation)
        if profile.provider != "fake":
            if not credential:
                raise ProviderExecutionError(
                    "The selected provider credential is missing."
                )
            assert profile.endpoint is not None
            self._context.network.assert_url_allowed(
                profile.endpoint, purpose="embedding provider"
            )
        input_tokens = sum(_estimate_tokens(value) for value in inputs)
        projected = _cost(profile, input_tokens, 0)
        snapshot = _price_snapshot(profile)
        reservation = self._reserve(
            operation_id,
            attempt_id,
            profile,
            projected,
            snapshot,
            allow_unknown_cost=allow_unknown_cost,
        )
        if profile.provider == "fake":
            vectors = tuple(
                _fake_embedding(value, profile.dimension) for value in inputs
            )
            self._settle(
                reservation,
                profile,
                projected,
                input_tokens,
                0,
                snapshot,
            )
            return ProviderEmbeddings(
                vectors,
                input_tokens,
                operation_id,
                attempt_id,
                projected,
                projected is not None,
            )
        assert profile.endpoint is not None
        try:
            response = self._client.post(
                profile.endpoint,
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "model": profile.model,
                    "input": inputs,
                    "dimensions": profile.dimension,
                    "encoding_format": "float",
                },
                timeout=_timeout(deadline, profile),
            )
        except httpx.HTTPError as exc:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The embedding provider did not return a usable response."
            ) from exc
        _check(deadline, cancellation, reservation, self, profile, snapshot)
        if response.status_code >= 500:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The embedding provider failed after request submission."
            )
        if response.status_code >= 400:
            self._settle(reservation, profile, Decimal("0"), 0, 0, snapshot)
            raise ProviderExecutionError(
                f"The embedding provider rejected the request ({response.status_code})."
            )
        try:
            payload = response.json()
            ordered = sorted(payload["data"], key=lambda item: item["index"])
            vectors = tuple(
                tuple(float(value) for value in item["embedding"]) for item in ordered
            )
            if len(vectors) != len(inputs) or any(
                len(vector) != profile.dimension for vector in vectors
            ):
                raise ValueError("embedding shape")
            actual_input = int(
                (payload.get("usage") or {}).get("prompt_tokens", input_tokens)
            )
        except (TypeError, ValueError, KeyError) as exc:
            self._uncertain(reservation, profile, snapshot)
            raise ProviderExecutionError(
                "The embedding provider response was malformed."
            ) from exc
        actual = _cost(profile, actual_input, 0)
        self._settle(reservation, profile, actual, actual_input, 0, snapshot)
        return ProviderEmbeddings(
            vectors,
            actual_input,
            operation_id,
            attempt_id,
            actual,
            actual is not None,
        )

    def _reserve(
        self,
        operation_id,
        attempt_id,
        profile,
        projected,
        snapshot,
        *,
        allow_unknown_cost=False,
    ):
        cost_known = profile.pricing_verified
        if not cost_known and not allow_unknown_cost:
            raise ProviderExecutionError(
                "This profile has unknown pricing. Automatic and capped paid work "
                "is disabled; a one-off manual request requires explicit "
                "unknown-cost approval and is outside USD budget caps."
            )
        return self._ledger.reserve(
            operation_id=operation_id,
            attempt_id=attempt_id,
            provider=profile.provider,
            profile_id=profile.id,
            projected_usd=projected or Decimal("0"),
            monthly_cap_usd=Decimal(self._context.settings.monthly_budget_usd),
            per_operation_cap_usd=Decimal(
                self._context.settings.per_operation_budget_usd
            ),
            timezone=self._context.settings.budget_timezone,
            price_snapshot=snapshot,
            max_concurrent=self._context.settings.max_concurrent_paid_requests,
            cost_known=cost_known,
        )

    def _settle(
        self, reservation, profile, actual, input_tokens, output_tokens, snapshot
    ):
        self._ledger.settle(
            reservation,
            # The ledger's monetary columns remain numeric. A zero amount on an
            # event whose snapshot says cost_known=false is an explicit sentinel,
            # not a claim that the provider charged nothing.
            actual_usd=actual if actual is not None else Decimal("0"),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            price_snapshot=snapshot,
            provider=profile.provider,
            profile_id=profile.id,
        )

    def _uncertain(self, reservation, profile, snapshot):
        self._ledger.mark_uncertain(
            reservation,
            price_snapshot=snapshot,
            provider=profile.provider,
            profile_id=profile.id,
        )


def _check(
    deadline: Deadline | None,
    cancellation: CancellationSignal | None,
    reservation: UsageReservation | None = None,
    gateway: ProviderGateway | None = None,
    profile: ProviderProfile | None = None,
    snapshot: dict[str, object] | None = None,
) -> None:
    try:
        if cancellation:
            cancellation.raise_if_cancelled()
        if deadline:
            deadline.raise_if_expired()
    except Exception:
        if reservation and gateway and profile and snapshot:
            gateway._uncertain(reservation, profile, snapshot)
        raise


def _timeout(deadline: Deadline | None, profile: ProviderProfile) -> float:
    if deadline:
        return max(
            0.1,
            min(deadline.remaining_seconds, profile.request_timeout_seconds),
        )
    return profile.request_timeout_seconds


def _estimate_tokens(text: str) -> int:
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _cost(
    profile: ProviderProfile, input_tokens: int, output_tokens: int
) -> Decimal | None:
    input_rate = profile.input_usd_per_million
    output_rate = profile.output_usd_per_million or Decimal("0")
    if input_rate is None:
        return None
    return (
        (Decimal(input_tokens) * input_rate + Decimal(output_tokens) * output_rate)
        / Decimal(1_000_000)
    ).quantize(Decimal("0.00000001"))


def _price_snapshot(profile: ProviderProfile) -> dict[str, object]:
    return {
        "profile_id": profile.id,
        "profile_version": profile.version,
        "provider": profile.provider,
        "model": profile.model,
        "endpoint": profile.endpoint,
        "cost_known": profile.pricing_verified,
        "input_usd_per_million": (
            str(profile.input_usd_per_million)
            if profile.input_usd_per_million is not None
            else None
        ),
        "output_usd_per_million": (
            str(profile.output_usd_per_million)
            if profile.output_usd_per_million is not None
            else None
        ),
        "effective_date": profile.price_effective_date,
        "source": profile.price_source,
    }


def _response_text(payload: dict) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    pieces = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and isinstance(
                content.get("text"), str
            ):
                pieces.append(content["text"])
    text = "\n".join(pieces).strip()
    if not text:
        raise ValueError("missing output text")
    return text


def _fake_embedding(text: str, dimension: int) -> tuple[float, ...]:
    import hashlib
    import math

    seed = hashlib.sha256(text.encode("utf-8")).digest()
    values = [
        ((seed[index % len(seed)] / 255.0) * 2.0) - 1.0 for index in range(dimension)
    ]
    magnitude = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / magnitude for value in values)
