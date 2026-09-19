from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import insert, select, update

from app.hpd.connector import BuildingCandidate, PropertySearchResponse
from app.storage.database import LocalStorage
from app.storage.schema import dossier_observations, property_identities

SUPPORTED_PANELS = {"hpd_violations"}
KNOWN_PANELS = {
    "hpd_violations",
    "hpd_complaints",
    "hpd_registration",
    "dob_permits",
    "dob_violations",
}


class DossierError(RuntimeError):
    pass


class PropertyDossierService:
    """Persist confirmed property identity separately from dated observations."""

    def __init__(self, storage: LocalStorage) -> None:
        self._storage = storage

    def resolve(
        self,
        result: PropertySearchResponse,
        *,
        confirmed_building_id: str | None = None,
    ) -> dict[str, object]:
        candidate = _confirmed_candidate(result, confirmed_building_id)
        if candidate is None:
            return {
                "status": "selection_required",
                "requires_selection": True,
                "candidates": [_candidate_payload(item) for item in result.candidates],
                "message": "Confirm one building before creating a dossier.",
            }
        identifiers = {
            "hpd_building_id": candidate.building_id,
            "hpd_registration_id": candidate.registration_id,
            "bbl": None,
            "bin": None,
        }
        display = " ".join(
            part
            for part in (
                candidate.house_number,
                candidate.street_name,
                candidate.borough,
                candidate.zip_code,
            )
            if part
        )
        normalized = _normalize_address(candidate)
        provenance = {
            "dataset_id": result.dataset_id,
            "dataset_url": result.dataset_url,
            "connector_version": result.connector_version,
            "resolved_at": result.fetched_at.isoformat(),
            "cache_status": result.cache_status,
            "confirmation": (
                "explicit_candidate" if confirmed_building_id else "unique_candidate"
            ),
        }
        now = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            existing = (
                connection.execute(
                    select(property_identities).where(
                        property_identities.c.normalized_address == normalized
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                identity_id = existing["id"]
                connection.execute(
                    update(property_identities)
                    .where(property_identities.c.id == identity_id)
                    .values(
                        display_address=display,
                        identifiers_json=_dump(identifiers),
                        provenance_json=_dump(provenance),
                        revision=existing["revision"] + 1,
                        updated_at=now,
                    )
                )
            else:
                identity_id = str(uuid.uuid4())
                connection.execute(
                    insert(property_identities).values(
                        id=identity_id,
                        entity_kind="building",
                        display_address=display,
                        normalized_address=normalized,
                        identifiers_json=_dump(identifiers),
                        provenance_json=_dump(provenance),
                        revision=1,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return {
            "status": "resolved",
            "requires_selection": False,
            **self.get_identity(identity_id),
        }

    def get_identity(self, identity_id: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(
                    select(property_identities).where(
                        property_identities.c.id == identity_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise DossierError("Property identity not found.")
        return _identity(row)

    def create_observation(
        self,
        identity_id: str,
        result: PropertySearchResponse,
        *,
        panels: list[str] | None = None,
    ) -> dict[str, object]:
        identity = self.get_identity(identity_id)
        requested = panels or ["hpd_violations"]
        if not requested or len(requested) > len(KNOWN_PANELS):
            raise DossierError("Select at least one supported dossier panel.")
        if any(panel not in KNOWN_PANELS for panel in requested):
            raise DossierError("Dossier request contains an unknown panel.")
        expected_building = identity["identifiers"].get("hpd_building_id")
        record_buildings = {
            row.building_id for row in result.records if row.building_id
        }
        if record_buildings and expected_building not in record_buildings:
            raise DossierError("Property result does not match the confirmed building.")
        panel_payload: dict[str, object] = {}
        for panel in requested:
            if panel == "hpd_violations":
                panel_payload[panel] = {
                    "status": "complete" if result.is_complete else "partial",
                    "dataset_id": result.dataset_id,
                    "dataset_url": result.dataset_url,
                    "connector_version": result.connector_version,
                    "observed_at": result.fetched_at.isoformat(),
                    "source_update_time": (
                        result.source_update_time.isoformat()
                        if result.source_update_time
                        else None
                    ),
                    "cache_status": result.cache_status,
                    "stale": result.stale,
                    "is_complete": result.is_complete,
                    "total_count": result.total_count,
                    "has_more": result.has_more or bool(result.continuation),
                    "records": [_jsonable(asdict(row)) for row in result.records],
                }
            else:
                panel_payload[panel] = {
                    "status": "unavailable",
                    "reason_code": "dataset_adapter_not_verified",
                    "message": (
                        "No reviewed official dataset adapter is configured for "
                        f"{panel.replace('_', ' ')}."
                    ),
                }
        payload = {
            "dossier_version": 1,
            "property_identity": identity,
            "panels": panel_payload,
            "timeline": _timeline(result),
            "limitations": [
                "Dataset panels are independent observations, not a single "
                "agency record.",
                "Unknown and unavailable fields are not inferred from addresses.",
            ],
        }
        encoded = _dump(payload)
        observation_id = str(uuid.uuid4())
        observed_at = datetime.now(UTC)
        with self._storage.state_engine.begin() as connection:
            connection.execute(
                insert(dossier_observations).values(
                    id=observation_id,
                    property_identity_id=identity_id,
                    requested_panels_json=_dump(requested),
                    payload_json=encoded,
                    payload_hash=hashlib.sha256(encoded.encode()).hexdigest(),
                    observed_at=observed_at,
                )
            )
        return self.get_observation(observation_id)

    def get_observation(self, observation_id: str) -> dict[str, object]:
        with self._storage.state_engine.connect() as connection:
            row = (
                connection.execute(
                    select(dossier_observations).where(
                        dossier_observations.c.id == observation_id
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise DossierError("Property dossier not found.")
        return {
            "id": row["id"],
            "property_identity_id": row["property_identity_id"],
            "requested_panels": json.loads(row["requested_panels_json"]),
            "payload": json.loads(row["payload_json"]),
            "payload_hash": row["payload_hash"],
            "observed_at": row["observed_at"].isoformat(),
        }

    def export(self, observation_id: str, directory: Path) -> Path:
        payload = self.get_observation(observation_id)
        content = (
            json.dumps(
                {
                    "export_version": 1,
                    "exported_at": datetime.now(UTC).isoformat(),
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=directory, prefix=".dossier-", suffix=".tmp"
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            final = directory / f"property-dossier-{observation_id[:8]}.json"
            os.replace(temporary, final)
            return final
        finally:
            temporary.unlink(missing_ok=True)


def _confirmed_candidate(
    result: PropertySearchResponse, confirmed_building_id: str | None
) -> BuildingCandidate | None:
    candidates = list(result.candidates)
    if not candidates and result.records:
        first = result.records[0]
        if all(
            (first.building_id, first.house_number, first.street_name, first.borough)
        ):
            candidates = [
                BuildingCandidate(
                    building_id=str(first.building_id),
                    registration_id=first.registration_id,
                    borough=str(first.borough),
                    house_number=str(first.house_number),
                    street_name=str(first.street_name),
                    zip_code=first.zip_code,
                )
            ]
    if confirmed_building_id:
        return next(
            (item for item in candidates if item.building_id == confirmed_building_id),
            None,
        )
    return candidates[0] if len(candidates) == 1 else None


def _candidate_payload(candidate: BuildingCandidate) -> dict[str, object]:
    return asdict(candidate)


def _identity(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "entity_kind": row["entity_kind"],
        "display_address": row["display_address"],
        "normalized_address": row["normalized_address"],
        "identifiers": json.loads(row["identifiers_json"]),
        "provenance": json.loads(row["provenance_json"]),
        "revision": row["revision"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


def _normalize_address(candidate: BuildingCandidate) -> str:
    return "|".join(
        str(part or "").strip().upper()
        for part in (
            candidate.borough,
            candidate.house_number,
            candidate.street_name,
            candidate.zip_code,
        )
    )


def _timeline(result: PropertySearchResponse) -> list[dict[str, object]]:
    events = []
    for row in result.records:
        for date_type, value, label in (
            ("inspection_date", row.inspection_date, "Violation inspected"),
            ("approved_date", row.approved_date, "Violation approved"),
            ("certified_date", row.certified_date, "Violation certified"),
            ("current_status_date", row.current_status_date, "Status updated"),
        ):
            if value:
                events.append(
                    {
                        "date": value,
                        "date_type": date_type,
                        "label": label,
                        "record_type": "hpd_violation",
                        "record_id": row.violation_id,
                        "status": row.current_status or row.violation_status,
                    }
                )
    return sorted(events, key=lambda item: (str(item["date"]), str(item["record_id"])))


def _jsonable(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    return value


def _dump(value: object) -> str:
    return json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
