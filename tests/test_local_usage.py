import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select

from app.providers.gateway import ProviderExecutionError, ProviderGateway
from app.providers.profiles import get_profile
from app.storage.database import LocalStorage
from app.storage.schema import usage_events
from app.usage.ledger import PaidCapacityUnavailable, SpendDenied, UsageLedger
from app.workspace.context import WorkspaceContext


def _workspace(tmp_path, *, offline=False):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    context = replace(
        context,
        settings=replace(context.settings, offline=offline),
        network=replace(context.network, offline=offline),
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    return context, storage


def _reserve(
    ledger,
    attempt,
    *,
    operation="operation",
    amount="1.00",
    now=None,
    monthly="15.00",
    max_concurrent=2,
):
    return ledger.reserve(
        operation_id=operation,
        attempt_id=attempt,
        provider="fixture",
        profile_id="fixture-v1",
        projected_usd=Decimal(amount),
        monthly_cap_usd=Decimal(monthly),
        per_operation_cap_usd=Decimal("10.00"),
        timezone="America/New_York",
        price_snapshot={"fixture": True},
        now=now,
        max_concurrent=max_concurrent,
    )


def test_reserve_settle_and_budget_denial_are_exact(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        ledger = UsageLedger(storage)
        reservation = _reserve(ledger, "attempt-1", amount="1.12345678")
        ledger.settle(
            reservation,
            actual_usd=Decimal("0.12345678"),
            input_tokens=10,
            output_tokens=20,
            price_snapshot={"fixture": True},
            provider="fixture",
            profile_id="fixture-v1",
        )
        summary = ledger.summary(
            monthly_cap_usd=Decimal("1.00"), timezone="America/New_York"
        )
        assert summary.settled_usd == Decimal("0.12345678")
        assert summary.remaining_usd == Decimal("0.87654322")
        with pytest.raises(SpendDenied, match="monthly"):
            _reserve(ledger, "attempt-2", amount="0.90", monthly="1.00")
    finally:
        storage.close()


def test_shared_paid_capacity_allows_two_outstanding_attempts(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        ledger = UsageLedger(storage)
        _reserve(ledger, "attempt-1", operation="one")
        _reserve(ledger, "attempt-2", operation="two")
        with pytest.raises(PaidCapacityUnavailable):
            _reserve(ledger, "attempt-3", operation="three")
    finally:
        storage.close()


def test_shared_paid_capacity_can_be_lowered_to_one(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        _reserve(UsageLedger(storage), "attempt-1", max_concurrent=1)
        with pytest.raises(PaidCapacityUnavailable):
            _reserve(UsageLedger(storage), "attempt-2", max_concurrent=1)
    finally:
        storage.close()


def test_prior_month_outstanding_reservation_remains_counted(tmp_path) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        ledger = UsageLedger(storage)
        _reserve(
            ledger,
            "attempt-1",
            amount="1.00",
            now=datetime(2026, 2, 1, 4, 30, tzinfo=UTC),
        )
        february = ledger.summary(
            monthly_cap_usd=Decimal("15"),
            timezone="America/New_York",
            now=datetime(2026, 2, 1, 5, 30, tzinfo=UTC),
        )
        assert february.month == "2026-02"
        assert february.reserved_usd == Decimal("1.00000000")
    finally:
        storage.close()


def test_gateway_settles_openai_response_and_sends_store_false(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "output_text": "Grounded answer [1].",
                "usage": {"input_tokens": 100, "output_tokens": 25},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        result = gateway.answer(
            prompt="Question and evidence",
            profile=get_profile("openai-answer-luna-v1"),
            credential="sk-fixture-not-a-real-key",
        )
        assert result.text == "Grounded answer [1]."
        assert b'"store":false' in captured["body"]
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(usage_events)) == 2
            )
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_gateway_streams_openai_text_deltas_and_settles_final_usage(tmp_path) -> None:
    context, storage = _workspace(tmp_path)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.read())
        events = [
            {
                "type": "response.output_text.delta",
                "delta": "Grounded answer ",
            },
            {"type": "response.output_text.delta", "delta": "[E1]."},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output_text": "Grounded answer [E1].",
                    "usage": {"input_tokens": 100, "output_tokens": 25},
                },
            },
        ]
        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=content,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    deltas = []
    try:
        result = gateway.answer(
            prompt="Question and evidence",
            profile=get_profile("openai-answer-luna-v1"),
            credential="sk-fixture-not-a-real-key",
            on_text_delta=deltas.append,
        )
        assert deltas == ["Grounded answer ", "[E1]."]
        assert result.text == "Grounded answer [E1]."
        assert captured["payload"]["stream"] is True
        assert captured["payload"]["stream_options"] == {
            "include_obfuscation": False
        }
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(usage_events)) == 2
            )
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_unknown_price_requires_one_off_approval_and_is_outside_usd_caps(
    tmp_path,
) -> None:
    context, storage = _workspace(tmp_path)
    context = replace(
        context,
        settings=replace(
            context.settings,
            monthly_budget_usd="0",
            per_operation_budget_usd="0",
        ),
    )
    profile = replace(
        get_profile("openai-answer-luna-v1"),
        provider="openai-compatible",
        input_usd_per_million=None,
        output_usd_per_million=None,
        price_effective_date=None,
        price_source=None,
    )
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "output_text": "Grounded answer [E1].",
                "usage": {"input_tokens": 100, "output_tokens": 25},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        with pytest.raises(ProviderExecutionError, match="unknown pricing"):
            gateway.answer(
                prompt="Question and evidence",
                profile=profile,
                credential="sk-fixture-not-a-real-key",
            )
        assert calls == 0
        result = gateway.answer(
            prompt="Question and evidence",
            profile=profile,
            credential="sk-fixture-not-a-real-key",
            allow_unknown_cost=True,
        )
        assert calls == 1
        assert result.cost_usd is None
        assert result.cost_known is False
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal("0"), timezone="America/New_York"
        )
        assert summary.settled_usd == 0
        assert summary.remaining_usd == 0
        assert summary.unknown_cost_attempts == 1
        assert summary.unknown_cost_in_flight == 0
        assert summary.unknown_cost_uncertain == 0
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_offline_denial_occurs_before_reservation(tmp_path) -> None:
    context, storage = _workspace(tmp_path, offline=True)
    gateway = ProviderGateway(context, UsageLedger(storage))
    try:
        with pytest.raises(Exception, match="Offline mode"):
            gateway.answer(
                prompt="Question",
                profile=get_profile("openai-answer-luna-v1"),
                credential="sk-fixture-not-a-real-key",
            )
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(usage_events)) == 0
            )
    finally:
        gateway.close()
        storage.close()


def test_lost_provider_response_is_conservatively_uncertain(tmp_path) -> None:
    context, storage = _workspace(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lost response")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        with pytest.raises(ProviderExecutionError, match="usable response"):
            gateway.answer(
                prompt="Question",
                profile=get_profile("openai-answer-luna-v1"),
                credential="sk-fixture-not-a-real-key",
            )
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert summary.uncertain_usd > 0
        assert summary.reserved_usd == 0
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_incomplete_provider_response_is_rejected_and_accounted_as_uncertain(
    tmp_path,
) -> None:
    context, storage = _workspace(tmp_path)
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "status": "incomplete",
                    "output_text": "Truncated text [E1]",
                    "incomplete_details": {"reason": "max_output_tokens"},
                },
            )
        )
    )
    gateway = ProviderGateway(context, UsageLedger(storage), client=client)
    try:
        with pytest.raises(ProviderExecutionError, match="malformed"):
            gateway.answer(
                prompt="Question",
                profile=get_profile("openai-answer-luna-v1"),
                credential="sk-fixture-not-a-real-key",
            )
        summary = UsageLedger(storage).summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert summary.uncertain_usd > 0
    finally:
        gateway.close()
        client.close()
        storage.close()


def test_uncertain_attempt_can_only_be_reconciled_by_append_only_correction(
    tmp_path,
) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        ledger = UsageLedger(storage)
        reservation = _reserve(ledger, "attempt-correction", amount="1.00")
        ledger.mark_uncertain(
            reservation,
            price_snapshot={"fixture": True},
            provider="fixture",
            profile_id="fixture-v1",
        )
        ledger.correct_uncertain(
            "attempt-correction",
            actual_usd=Decimal("0.25"),
            reason="Provider billing record reviewed",
        )
        summary = ledger.summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert summary.uncertain_usd == 0
        assert summary.settled_usd == Decimal("0.25000000")
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(usage_events)) == 3
            )
    finally:
        storage.close()


def test_reconciled_unknown_cost_becomes_known_without_rewriting_history(
    tmp_path,
) -> None:
    _context, storage = _workspace(tmp_path)
    try:
        ledger = UsageLedger(storage)
        reservation = ledger.reserve(
            operation_id="unknown-operation",
            attempt_id="unknown-attempt",
            provider="fixture",
            profile_id="fixture-unknown",
            projected_usd=Decimal("0"),
            monthly_cap_usd=Decimal("15"),
            per_operation_cap_usd=Decimal("2"),
            timezone="America/New_York",
            price_snapshot={"pricing": "unknown"},
            cost_known=False,
        )
        ledger.mark_uncertain(
            reservation,
            price_snapshot={"pricing": "unknown", "cost_known": False},
            provider="fixture",
            profile_id="fixture-unknown",
        )
        before = ledger.summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert before.unknown_cost_uncertain == 1

        ledger.correct_uncertain(
            "unknown-attempt",
            actual_usd=Decimal("0.25"),
            reason="Provider invoice supplied an exact amount",
        )
        after = ledger.summary(
            monthly_cap_usd=Decimal("15"), timezone="America/New_York"
        )
        assert after.settled_usd == Decimal("0.25000000")
        assert after.unknown_cost_attempts == 0
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(usage_events)) == 3
            )
    finally:
        storage.close()
