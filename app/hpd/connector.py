from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from importlib.resources import files

import httpx

from app.jobs.runtime import Deadline
from app.workspace.network import NetworkPolicy

BOROUGHS = {
    "1": "MANHATTAN",
    "2": "BRONX",
    "3": "BROOKLYN",
    "4": "QUEENS",
    "5": "STATEN ISLAND",
    "MN": "MANHATTAN",
    "BX": "BRONX",
    "BK": "BROOKLYN",
    "QN": "QUEENS",
    "SI": "STATEN ISLAND",
}
STREET_SUFFIXES = {
    "ST": "STREET",
    "ST.": "STREET",
    "AVE": "AVENUE",
    "AVE.": "AVENUE",
    "RD": "ROAD",
    "BLVD": "BOULEVARD",
}
STATUS_FILTER_MAPPING = {"open": "Open", "closed": "Close"}
HPD_OUTBOUND_CONCURRENCY = 4
_HPD_REQUEST_SLOTS = threading.BoundedSemaphore(HPD_OUTBOUND_CONCURRENCY)


class PropertyConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class PropertyQuery:
    building_id: str | None = None
    registration_id: str | None = None
    house_number: str | None = None
    street_name: str | None = None
    borough: str | None = None
    zip_code: str | None = None
    violation_class: str | None = None
    status: str | None = None
    inspection_date_from: str | None = None
    inspection_date_to: str | None = None
    limit: int = 50
    continuation: str | None = None

    def validate(self) -> PropertyQuery:
        identifiers = bool(self.building_id or self.registration_id)
        address = bool(self.house_number and self.street_name)
        if not identifiers and not address:
            raise ValueError(
                "Provide a building ID, registration ID, or house number and street."
            )
        if bool(self.house_number) != bool(self.street_name):
            raise ValueError("House number and street name must be supplied together.")
        if self.house_number:
            _house(self.house_number)
        if self.street_name:
            _street(self.street_name)
        if self.borough:
            _borough(self.borough)
        if self.zip_code and not re.fullmatch(r"\d{5}", self.zip_code):
            raise ValueError("ZIP code must contain five digits.")
        for name, value in (
            ("building ID", self.building_id),
            ("registration ID", self.registration_id),
        ):
            if value and not value.isdigit():
                raise ValueError(f"{name} must contain digits only.")
        if not 1 <= self.limit <= 100:
            raise ValueError("Property result limit must be between 1 and 100.")
        if self.violation_class and self.violation_class.upper() not in {
            "A",
            "B",
            "C",
            "I",
        }:
            raise ValueError("Violation class must be A, B, C, or I.")
        if self.status and self.status.lower() not in {"open", "closed"}:
            raise ValueError("Status must be open or closed.")
        date_from = (
            _inspection_date(self.inspection_date_from)
            if self.inspection_date_from
            else None
        )
        date_to = (
            _inspection_date(self.inspection_date_to)
            if self.inspection_date_to
            else None
        )
        if date_from and date_to and date_from > date_to:
            raise ValueError("Inspection start date must be on or before the end date.")
        return self

    def identity_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload.pop("continuation")
        return payload


@dataclass(frozen=True)
class BuildingCandidate:
    building_id: str
    registration_id: str | None
    borough: str
    house_number: str
    street_name: str
    zip_code: str | None


@dataclass(frozen=True)
class HpdViolationRecord:
    violation_id: str
    building_id: str | None
    registration_id: str | None
    borough: str | None
    house_number: str | None
    street_name: str | None
    zip_code: str | None
    apartment: str | None
    violation_class: str | None
    inspection_date: str | None
    approved_date: str | None
    certified_date: str | None
    order_number: str | None
    nov_id: str | None
    description: str | None
    current_status: str | None
    current_status_date: str | None
    violation_status: str | None


@dataclass(frozen=True)
class PropertySearchResponse:
    query: PropertyQuery
    candidates: tuple[BuildingCandidate, ...]
    records: tuple[HpdViolationRecord, ...]
    requires_selection: bool
    continuation: str | None
    is_complete: bool
    returned_count: int
    fetched_at: datetime
    dataset_id: str
    dataset_url: str
    connector_version: str
    source_status: str
    total_count: int | None = None
    has_more: bool = False
    next_cursor: str | None = None
    source_update_time: datetime | None = None
    fetch_started_at: datetime | None = None
    fetch_completed_at: datetime | None = None
    cache_status: str = "live"
    stale: bool = False


def load_hpd_manifest() -> dict:
    resource = files("app.resources").joinpath("sources/hpd_violations.json")
    payload = json.loads(resource.read_text(encoding="utf-8"))
    if (
        payload.get("format_version") != 1
        or payload.get("dataset_id") != "wvxf-dwi5"
        or payload.get("status_mapping") != STATUS_FILTER_MAPPING
    ):
        raise PropertyConnectorError("The packaged HPD dataset manifest is invalid.")
    return payload


class HpdSocrataConnector:
    def __init__(
        self,
        network: NetworkPolicy,
        *,
        app_token: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.manifest = load_hpd_manifest()
        self._network = network
        self._app_token = app_token
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def search(
        self, query: PropertyQuery, *, deadline: Deadline | None = None
    ) -> PropertySearchResponse:
        query.validate()
        fetch_started_at = datetime.now(UTC)
        endpoint = self.manifest["endpoint"]
        self._network.assert_url_allowed(endpoint, purpose="HPD property lookup")
        headers = {"Accept": "application/json"}
        if self._app_token:
            headers["X-App-Token"] = self._app_token
        where = _where_clause(query)
        if not query.building_id and not query.continuation:
            candidates = self._resolve_candidates(endpoint, query, headers, deadline)
            if len(candidates) != 1:
                fetch_completed_at = datetime.now(UTC)
                return PropertySearchResponse(
                    query=query,
                    candidates=candidates,
                    records=(),
                    requires_selection=len(candidates) > 1,
                    continuation=None,
                    is_complete=len(candidates) == 0,
                    returned_count=0,
                    fetched_at=fetch_completed_at,
                    dataset_id=self.manifest["dataset_id"],
                    dataset_url=self.manifest["dataset_url"],
                    connector_version=self.manifest["connector_version"],
                    source_status="verified_zero" if not candidates else "success",
                    total_count=0 if not candidates else None,
                    fetch_started_at=fetch_started_at,
                    fetch_completed_at=fetch_completed_at,
                )
            where = f"({where}) AND buildingid = {_literal(candidates[0].building_id)}"
        cursor = _decode_continuation(query) if query.continuation else None
        null_partition = cursor is not None and cursor["inspection_date"] is None
        if cursor:
            if null_partition:
                cursor_filter = f"violationid > {_literal(cursor['violation_id'])}"
            else:
                cursor_filter = (
                    f"(inspectiondate < {_literal(cursor['inspection_date'])} OR "
                    f"(inspectiondate = {_literal(cursor['inspection_date'])} AND "
                    f"violationid > {_literal(cursor['violation_id'])}))"
                )
            where = (
                f"({where}) AND buildingid = {_literal(cursor['building_id'])} "
                f"AND {cursor_filter}"
            )
        params = {
            "$select": ",".join(self.manifest["fields"]),
            "$where": (
                f"({where}) AND inspectiondate is "
                f"{'null' if null_partition else 'not null'}"
            ),
            "$order": "violationid ASC"
            if null_partition
            else "inspectiondate DESC,violationid ASC",
            "$limit": str(query.limit + 1),
        }
        response = self._request(endpoint, params, headers, deadline)
        try:
            rows = response.json()
        except ValueError as exc:
            raise PropertyConnectorError("HPD returned invalid JSON.") from exc
        if not isinstance(rows, list):
            raise PropertyConnectorError("HPD response was not a row list.")
        if (
            not null_partition
            and len(rows) <= query.limit
            and not query.inspection_date_from
            and not query.inspection_date_to
        ):
            # Dates sort separately so nulls neither disappear nor require a
            # publisher-specific NULLS LAST ordering extension.
            null_where = _where_clause(query)
            building_id = (
                cursor["building_id"]
                if cursor
                else (query.building_id or candidates[0].building_id)
            )
            null_params = {
                "$select": params["$select"],
                "$where": (
                    f"({null_where}) AND buildingid = {_literal(building_id)} "
                    "AND inspectiondate is null"
                ),
                "$order": "violationid ASC",
                "$limit": str(query.limit + 1 - len(rows)),
            }
            null_response = self._request(endpoint, null_params, headers, deadline)
            try:
                null_rows = null_response.json()
            except ValueError as exc:
                raise PropertyConnectorError("HPD returned invalid JSON.") from exc
            if not isinstance(null_rows, list):
                raise PropertyConnectorError("HPD response was not a row list.")
            rows.extend(null_rows)
        records = tuple(_record(row) for row in rows[: query.limit])
        candidates = _candidates(records)
        requires_selection = not query.building_id and len(candidates) > 1
        if requires_selection:
            records = ()
        has_more = len(rows) > query.limit and bool(records)
        continuation = (
            _continuation(query, records[-1]) if has_more and records else None
        )
        fetch_completed_at = datetime.now(UTC)
        return PropertySearchResponse(
            query=query,
            candidates=candidates,
            records=records,
            requires_selection=requires_selection,
            continuation=continuation,
            is_complete=not has_more and not requires_selection,
            returned_count=len(records),
            fetched_at=fetch_completed_at,
            dataset_id=self.manifest["dataset_id"],
            dataset_url=self.manifest["dataset_url"],
            connector_version=self.manifest["connector_version"],
            source_status="verified_zero" if not rows else "success",
            total_count=0 if not rows else None,
            has_more=has_more,
            next_cursor=continuation,
            source_update_time=_source_update_time(response),
            fetch_started_at=fetch_started_at,
            fetch_completed_at=fetch_completed_at,
        )

    def _resolve_candidates(self, endpoint, query, headers, deadline):
        fields = "buildingid,registrationid,boro,housenumber,streetname,zip"
        params = {
            "$select": fields,
            "$where": _identity_where_clause(query),
            "$group": fields,
            "$order": "buildingid ASC",
            "$limit": "101",
        }
        response = self._request(endpoint, params, headers, deadline)
        try:
            rows = response.json()
        except ValueError as exc:
            raise PropertyConnectorError("HPD returned invalid JSON.") from exc
        if not isinstance(rows, list):
            raise PropertyConnectorError("HPD response was not a row list.")
        if len(rows) > 100:
            raise PropertyConnectorError(
                "Property identity matched more than 100 buildings; add a "
                "borough or ZIP."
            )
        return tuple(_candidate(row) for row in rows)

    def _request(self, endpoint, params, headers, deadline):
        last_error = None
        for attempt in range(3):
            if deadline:
                deadline.raise_if_expired()
            timeout = min(15.0, deadline.remaining_seconds) if deadline else 15.0
            wait_timeout = max(0.1, timeout)
            if not _HPD_REQUEST_SLOTS.acquire(timeout=wait_timeout):
                raise PropertyConnectorError(
                    "HPD request capacity was unavailable before the deadline."
                )
            try:
                try:
                    response = self._client.get(
                        endpoint,
                        params=params,
                        headers=headers,
                        timeout=max(0.1, timeout),
                    )
                finally:
                    _HPD_REQUEST_SLOTS.release()
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt == 2:
                    break
                continue
            if response.status_code == 429 and attempt < 2:
                delay = min(float(response.headers.get("Retry-After", "0.1")), 1.0)
                if deadline:
                    delay = min(delay, deadline.remaining_seconds)
                time.sleep(max(0, delay))
                continue
            if response.status_code >= 500 and attempt < 2:
                continue
            if response.status_code >= 400:
                raise PropertyConnectorError(
                    f"HPD rejected the bounded request ({response.status_code})."
                )
            return response
        raise PropertyConnectorError("HPD is temporarily unavailable.") from last_error


def _where_clause(query: PropertyQuery) -> str:
    clauses = [_identity_where_clause(query)]
    if query.violation_class:
        clauses.append(f"upper(class) = {_literal(query.violation_class.upper())}")
    if query.status:
        status = STATUS_FILTER_MAPPING[query.status.lower()]
        clauses.append(f"violationstatus = {_literal(status)}")
    if query.inspection_date_from:
        start = _inspection_date(query.inspection_date_from)
        clauses.append(f"inspectiondate >= {_literal(_socrata_date(start))}")
    if query.inspection_date_to:
        end_exclusive = _inspection_date(query.inspection_date_to) + timedelta(days=1)
        clauses.append(f"inspectiondate < {_literal(_socrata_date(end_exclusive))}")
    return " AND ".join(clauses)


def _inspection_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Inspection dates must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError("Inspection dates must use YYYY-MM-DD format.")
    return parsed


def _socrata_date(value: date) -> str:
    return value.isoformat() + "T00:00:00.000"


def _source_update_time(response: httpx.Response) -> datetime | None:
    value = response.headers.get("Last-Modified")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _identity_where_clause(query: PropertyQuery) -> str:
    clauses = []
    if query.building_id:
        clauses.append(f"buildingid = {_literal(query.building_id)}")
    if query.registration_id:
        clauses.append(f"registrationid = {_literal(query.registration_id)}")
    if query.house_number and query.street_name:
        clauses.extend(
            [
                f"upper(housenumber) = {_literal(_house(query.house_number))}",
                f"upper(streetname) = {_literal(_street(query.street_name))}",
            ]
        )
    if query.borough:
        clauses.append(f"upper(boro) = {_literal(_borough(query.borough))}")
    if query.zip_code:
        if not re.fullmatch(r"\d{5}", query.zip_code):
            raise ValueError("ZIP code must contain five digits.")
        clauses.append(f"zip = {_literal(query.zip_code)}")
    return " AND ".join(clauses)


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _house(value: str) -> str:
    normalized = " ".join(value.upper().split())
    if not re.fullmatch(r"[0-9A-Z -]{1,30}", normalized):
        raise ValueError("House number contains unsupported characters.")
    return normalized


def _street(value: str) -> str:
    normalized = " ".join(value.upper().replace(",", " ").split())
    parts = normalized.split()
    if parts and parts[-1] in STREET_SUFFIXES:
        parts[-1] = STREET_SUFFIXES[parts[-1]]
    normalized = " ".join(parts)
    if not re.fullmatch(r"[0-9A-Z .'-]{1,100}", normalized):
        raise ValueError("Street name contains unsupported characters.")
    return normalized


def _borough(value: str) -> str:
    normalized = " ".join(value.upper().split())
    normalized = BOROUGHS.get(normalized, normalized)
    if normalized not in set(BOROUGHS.values()):
        raise ValueError("Borough is not recognized.")
    return normalized


def _record(row: dict) -> HpdViolationRecord:
    if not isinstance(row, dict) or not str(row.get("violationid", "")).isdigit():
        raise PropertyConnectorError("HPD row identity is missing or invalid.")
    return HpdViolationRecord(
        violation_id=str(row["violationid"]),
        building_id=_optional(row, "buildingid"),
        registration_id=_optional(row, "registrationid"),
        borough=_optional(row, "boro"),
        house_number=_optional(row, "housenumber"),
        street_name=_optional(row, "streetname"),
        zip_code=_optional(row, "zip"),
        apartment=_optional(row, "apartment"),
        violation_class=_optional(row, "class"),
        inspection_date=_optional(row, "inspectiondate"),
        approved_date=_optional(row, "approveddate"),
        certified_date=_optional(row, "certifieddate"),
        order_number=_optional(row, "ordernumber"),
        nov_id=_optional(row, "novid"),
        description=_optional(row, "novdescription"),
        current_status=_optional(row, "currentstatus"),
        current_status_date=_optional(row, "currentstatusdate"),
        violation_status=_optional(row, "violationstatus"),
    )


def _optional(row: dict, key: str) -> str | None:
    value = row.get(key)
    return str(value) if value not in (None, "") else None


def _candidates(
    records: tuple[HpdViolationRecord, ...],
) -> tuple[BuildingCandidate, ...]:
    candidates = {}
    for row in records:
        if not row.building_id:
            continue
        candidates[row.building_id] = BuildingCandidate(
            building_id=row.building_id,
            registration_id=row.registration_id,
            borough=row.borough or "Unknown",
            house_number=row.house_number or "Unknown",
            street_name=row.street_name or "Unknown",
            zip_code=row.zip_code,
        )
    return tuple(candidates[key] for key in sorted(candidates))


def _candidate(row: dict) -> BuildingCandidate:
    building_id = _optional(row, "buildingid") if isinstance(row, dict) else None
    if not building_id or not building_id.isdigit():
        raise PropertyConnectorError("HPD building candidate identity is invalid.")
    return BuildingCandidate(
        building_id=building_id,
        registration_id=_optional(row, "registrationid"),
        borough=_optional(row, "boro") or "Unknown",
        house_number=_optional(row, "housenumber") or "Unknown",
        street_name=_optional(row, "streetname") or "Unknown",
        zip_code=_optional(row, "zip"),
    )


def _continuation(query: PropertyQuery, record: HpdViolationRecord) -> str:
    payload = {
        "request_hash": _request_hash(query),
        "building_id": record.building_id,
        "inspection_date": record.inspection_date,
        "violation_id": record.violation_id,
    }
    return base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).decode()


def _decode_continuation(query: PropertyQuery) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(query.continuation).decode())
    except Exception as exc:
        raise ValueError("Property continuation is invalid.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("request_hash") != _request_hash(query)
        or not str(payload.get("building_id", "")).isdigit()
        or "inspection_date" not in payload
        or (
            payload["inspection_date"] is not None
            and not isinstance(payload["inspection_date"], str)
        )
        or not str(payload.get("violation_id", "")).isdigit()
    ):
        raise ValueError("Property continuation does not match this request.")
    return payload


def _request_hash(query: PropertyQuery) -> str:
    return hashlib.sha256(
        json.dumps(
            query.identity_dict(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
