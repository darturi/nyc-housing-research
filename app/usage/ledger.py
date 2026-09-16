from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select

from app.storage.database import LocalStorage
from app.storage.schema import maintenance_state, paid_call_leases, usage_events

ZERO = Decimal("0")


class UsageEventType(StrEnum):
    RESERVE = "reserve"
    SETTLE = "settle"
    UNCERTAIN = "uncertain"
    CORRECTION = "correction"


class SpendDenied(RuntimeError):
    pass


class PaidCapacityUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageReservation:
    operation_id: str
    attempt_id: str
    reserved_usd: Decimal
    cost_known: bool = True


@dataclass(frozen=True)
class UsageSummary:
    month: str
    timezone: str
    cap_usd: Decimal
    settled_usd: Decimal
    reserved_usd: Decimal
    uncertain_usd: Decimal
    remaining_usd: Decimal
    unknown_cost_attempts: int
    unknown_cost_in_flight: int
    unknown_cost_uncertain: int
    history_pruned_before: str | None


class UsageLedger:
    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def reserve(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        provider: str,
        profile_id: str,
        projected_usd: Decimal,
        monthly_cap_usd: Decimal,
        per_operation_cap_usd: Decimal,
        timezone: str,
        price_snapshot: dict[str, object],
        now: datetime | None = None,
        lease_seconds: int = 120,
        max_concurrent: int = 2,
        cost_known: bool = True,
    ) -> UsageReservation:
        now = now or datetime.now(UTC)
        projected_usd = _money(projected_usd)
        if projected_usd < 0:
            raise ValueError("Projected spend cannot be negative.")
        if not cost_known and projected_usd != ZERO:
            raise ValueError("Unknown-cost reservations cannot claim a USD amount.")
        price_snapshot = {**price_snapshot, "cost_known": cost_known}
        with _ImmediateTransaction(self._storage.state_engine) as connection:
            if connection.scalar(
                select(maintenance_state.c.active).where(maintenance_state.c.id == 1)
            ):
                raise PaidCapacityUnavailable(
                    "Workspace maintenance is active; paid work was not admitted."
                )
            events = _event_rows(connection)
            if any(row["attempt_id"] == attempt_id for row in events):
                raise SpendDenied(
                    "This provider attempt has already been accounted for."
                )
            states = _attempt_states(events)
            month = _month(now, timezone)
            month_total = sum(
                (
                    state["amount"]
                    for state in states.values()
                    if _month(_aware(state["reserved_at"]), timezone) == month
                    and state["status"] in {"reserved", "settled", "uncertain"}
                ),
                ZERO,
            )
            outstanding_prior = sum(
                (
                    state["amount"]
                    for state in states.values()
                    if _month(_aware(state["reserved_at"]), timezone) != month
                    and state["status"] == "reserved"
                ),
                ZERO,
            )
            operation_total = sum(
                (
                    state["amount"]
                    for state in states.values()
                    if state["operation_id"] == operation_id
                    and state["status"] in {"reserved", "settled", "uncertain"}
                ),
                ZERO,
            )
            if cost_known and operation_total + projected_usd > per_operation_cap_usd:
                raise SpendDenied(
                    "The operation would exceed its configured spend ceiling."
                )
            if (
                cost_known
                and month_total + outstanding_prior + projected_usd > monthly_cap_usd
            ):
                raise SpendDenied(
                    "The request would exceed the local monthly API budget."
                )
            connection.execute(
                delete(paid_call_leases).where(paid_call_leases.c.expires_at <= now)
            )
            lease_count = len(
                list(connection.scalars(select(paid_call_leases.c.attempt_id)))
            )
            if lease_count >= max_concurrent:
                raise PaidCapacityUnavailable(
                    "The configured concurrent paid-request limit is already "
                    "active in this workspace."
                )
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    operation_id=operation_id,
                    attempt_id=attempt_id,
                    event_type=UsageEventType.RESERVE.value,
                    provider=provider,
                    profile_id=profile_id,
                    amount_usd=projected_usd,
                    input_tokens=None,
                    output_tokens=None,
                    price_snapshot_json=json.dumps(price_snapshot, sort_keys=True),
                    status="reserved",
                    created_at=now,
                )
            )
            connection.execute(
                insert(paid_call_leases).values(
                    attempt_id=attempt_id,
                    operation_id=operation_id,
                    expires_at=now + timedelta(seconds=lease_seconds),
                    created_at=now,
                )
            )
        return UsageReservation(operation_id, attempt_id, projected_usd, cost_known)

    def settle(
        self,
        reservation: UsageReservation,
        *,
        actual_usd: Decimal,
        input_tokens: int | None,
        output_tokens: int | None,
        price_snapshot: dict[str, object],
        provider: str,
        profile_id: str,
        now: datetime | None = None,
    ) -> None:
        self._finish(
            reservation,
            event_type=UsageEventType.SETTLE,
            amount=actual_usd,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            price_snapshot=price_snapshot,
            provider=provider,
            profile_id=profile_id,
            now=now,
        )

    def mark_uncertain(
        self,
        reservation: UsageReservation,
        *,
        price_snapshot: dict[str, object],
        provider: str,
        profile_id: str,
        now: datetime | None = None,
    ) -> None:
        self._finish(
            reservation,
            event_type=UsageEventType.UNCERTAIN,
            amount=reservation.reserved_usd,
            input_tokens=None,
            output_tokens=None,
            price_snapshot=price_snapshot,
            provider=provider,
            profile_id=profile_id,
            now=now,
        )

    def summary(
        self,
        *,
        monthly_cap_usd: Decimal,
        timezone: str,
        now: datetime | None = None,
    ) -> UsageSummary:
        now = now or datetime.now(UTC)
        with self._storage.state_engine.connect() as connection:
            states = _attempt_states(_event_rows(connection))
        month = _month(now, timezone)
        amounts = {"reserved": ZERO, "settled": ZERO, "uncertain": ZERO}
        unknown = {"reserved": 0, "settled": 0, "uncertain": 0}
        for state in states.values():
            if (
                _month(_aware(state["reserved_at"]), timezone) == month
                or state["status"] == "reserved"
            ):
                status = state["status"]
                if status in amounts:
                    amounts[status] += state["amount"]
                    if not state["cost_known"]:
                        unknown[status] += 1
        used = sum(amounts.values(), ZERO)
        return UsageSummary(
            month=month,
            timezone=timezone,
            cap_usd=_money(monthly_cap_usd),
            settled_usd=_money(amounts["settled"]),
            reserved_usd=_money(amounts["reserved"]),
            uncertain_usd=_money(amounts["uncertain"]),
            remaining_usd=max(ZERO, _money(monthly_cap_usd) - used),
            unknown_cost_attempts=sum(unknown.values()),
            unknown_cost_in_flight=unknown["reserved"],
            unknown_cost_uncertain=unknown["uncertain"],
            history_pruned_before=self._history_pruned_before(),
        )

    def _history_pruned_before(self) -> str | None:
        # Imported lazily so the retention service can use ledger table semantics
        # without creating an import cycle.
        from app.maintenance.retention import usage_history_pruned_before

        return usage_history_pruned_before(self._storage)

    def correct_uncertain(
        self,
        attempt_id: str,
        *,
        actual_usd: Decimal,
        reason: str,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Append an explicit reconciliation without rewriting ledger history."""
        if not reason.strip() or len(reason) > 500:
            raise ValueError("A bounded correction reason is required.")
        now = now or datetime.now(UTC)
        with _ImmediateTransaction(self._storage.state_engine) as connection:
            rows = _event_rows(connection)
            states = _attempt_states(rows)
            state = states.get(attempt_id)
            if state is None or state["status"] != "uncertain":
                raise SpendDenied(
                    "Only an uncertain provider attempt can be corrected."
                )
            previous = next(
                row
                for row in rows
                if row["attempt_id"] == attempt_id
                and row["event_type"] == UsageEventType.UNCERTAIN.value
            )
            snapshot = json.loads(previous["price_snapshot_json"])
            snapshot["correction_reason"] = reason.strip()
            snapshot["cost_known"] = True
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    operation_id=state["operation_id"],
                    attempt_id=attempt_id,
                    event_type=UsageEventType.CORRECTION.value,
                    provider=previous["provider"],
                    profile_id=previous["profile_id"],
                    amount_usd=_money(actual_usd),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    price_snapshot_json=json.dumps(snapshot, sort_keys=True),
                    status="settled",
                    created_at=now,
                )
            )

    def _finish(
        self,
        reservation: UsageReservation,
        *,
        event_type: UsageEventType,
        amount: Decimal,
        input_tokens: int | None,
        output_tokens: int | None,
        price_snapshot: dict[str, object],
        provider: str,
        profile_id: str,
        now: datetime | None,
    ) -> None:
        now = now or datetime.now(UTC)
        with _ImmediateTransaction(self._storage.state_engine) as connection:
            states = _attempt_states(_event_rows(connection))
            state = states.get(reservation.attempt_id)
            if state is None or state["status"] != "reserved":
                raise SpendDenied("Provider attempt is not an outstanding reservation.")
            connection.execute(
                insert(usage_events).values(
                    id=str(uuid.uuid4()),
                    operation_id=reservation.operation_id,
                    attempt_id=reservation.attempt_id,
                    event_type=event_type.value,
                    provider=provider,
                    profile_id=profile_id,
                    amount_usd=_money(amount),
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    price_snapshot_json=json.dumps(price_snapshot, sort_keys=True),
                    status=(
                        "settled"
                        if event_type == UsageEventType.SETTLE
                        else event_type.value
                    ),
                    created_at=now,
                )
            )
            connection.execute(
                delete(paid_call_leases).where(
                    paid_call_leases.c.attempt_id == reservation.attempt_id
                )
            )


def _event_rows(connection) -> list[dict]:
    return [
        dict(row)
        for row in connection.execute(
            select(usage_events).order_by(usage_events.c.created_at, usage_events.c.id)
        ).mappings()
    ]


def _attempt_states(events: list[dict]) -> dict[str, dict]:
    grouped: dict[str, list[dict]] = {}
    for row in events:
        grouped.setdefault(row["attempt_id"], []).append(row)
    states: dict[str, dict] = {}
    for attempt_id, rows in grouped.items():
        by_type = {row["event_type"]: row for row in rows}
        reserve = by_type.get(UsageEventType.RESERVE.value)
        if reserve is None:
            continue
        final = (
            by_type.get(UsageEventType.CORRECTION.value)
            or by_type.get(UsageEventType.SETTLE.value)
            or by_type.get(UsageEventType.UNCERTAIN.value)
            or reserve
        )
        states[attempt_id] = {
            "operation_id": reserve["operation_id"],
            "reserved_at": reserve["created_at"],
            "status": final["status"],
            "amount": Decimal(final["amount_usd"]),
            "cost_known": _cost_known(final),
        }
    return states


def _cost_known(reserve: dict) -> bool:
    try:
        snapshot = json.loads(reserve["price_snapshot_json"])
    except (KeyError, TypeError, ValueError):
        return True
    return snapshot.get("cost_known") is not False


def _month(now: datetime, timezone: str) -> str:
    return now.astimezone(ZoneInfo(timezone)).strftime("%Y-%m")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _money(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("Spend amount must be finite.")
    return value.quantize(Decimal("0.00000001"))


class _ImmediateTransaction:
    def __init__(self, engine) -> None:
        self._engine = engine
        self._connection = None

    def __enter__(self):
        self._connection = self._engine.connect()
        self._connection.exec_driver_sql("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        assert self._connection is not None
        try:
            if exc_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        finally:
            self._connection.close()
