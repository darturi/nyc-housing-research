import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session as DbSession

from app.ingestion.artifacts import read_artifact, write_artifact
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    download_url,
    hash_bytes,
)
from app.models.hpd_ingestion_checkpoint import HpdIngestionCheckpoint
from app.models.hpd_violation import HpdViolation
from app.models.source import Source
from app.models.source_version import SourceVersion

HPD_PAGE_SIZE = 50_000
HPD_DELTA_OVERLAP_DAYS = 14
HPD_UPSERT_BATCH_SIZE = 1_000


def parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value[:26], fmt).date()
        except ValueError:
            continue
    return None


def field(record: dict[str, Any], *names: str) -> str | None:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return str(value)
    return None


def external_id_for_record(record: dict[str, Any]) -> str:
    external_id = field(record, "violationid", "violation_id", "id")
    if external_id:
        return external_id
    return hash_bytes(json.dumps(record, sort_keys=True).encode("utf-8"))


def normalize_address_part(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"[^A-Z0-9]", "", value.upper())
    return normalized or None


def normalized_full_address(
    house_number: str | None,
    street_name: str | None,
) -> str | None:
    parts = [normalize_address_part(house_number), normalize_address_part(street_name)]
    value = " ".join(part for part in parts if part)
    return value or None


def source_version_for_records(
    db: DbSession,
    source: Source,
    records: list[dict[str, Any]],
) -> SourceVersion:
    content = json.dumps(records, sort_keys=True).encode("utf-8")
    artifact = DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type="application/json",
        byte_size=len(content),
        extension="json",
        source_url=source.source_url,
    )
    return create_or_get_source_version(db, source, artifact)


def upsert_hpd_violations(
    db: DbSession,
    source: Source,
    records: list[dict[str, Any]],
    source_version: SourceVersion | None = None,
) -> tuple[SourceVersion, int, int, int]:
    """Upsert every supplied row; pagination, not a row limit, controls volume."""
    if source_version is None:
        source_version = source_version_for_records(db, source, records)

    rows = [violation_values(source, source_version, record) for record in records]
    if db.get_bind().dialect.name == "postgresql":
        return bulk_upsert_hpd_violations(db, source_version, rows)

    created = 0
    updated = 0
    skipped = 0
    for values in rows:
        existing = db.scalar(
            select(HpdViolation).where(
                HpdViolation.external_id == values["external_id"]
            )
        )
        if existing is None:
            db.add(HpdViolation(**values))
            created += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
    db.commit()
    return source_version, created, updated, skipped


def violation_values(
    source: Source,
    source_version: SourceVersion,
    record: dict[str, Any],
) -> dict[str, Any]:
    house_number = field(record, "housenumber", "house_number")
    street_name = field(record, "streetname", "street_name")
    return {
        "source_id": source.id,
        "source_version_id": source_version.id,
        "external_id": external_id_for_record(record),
        "building_id": field(record, "buildingid", "building_id"),
        "registration_id": field(record, "registrationid", "registration_id"),
        "boro": field(record, "boro", "borough"),
        "house_number": house_number,
        "normalized_house_number": normalize_address_part(house_number),
        "street_name": street_name,
        "normalized_street_name": normalize_address_part(street_name),
        "normalized_full_address": normalized_full_address(house_number, street_name),
        "zip_code": field(record, "zip", "zip_code"),
        "apartment": field(record, "apartment"),
        "violation_class": field(record, "class", "violationclass"),
        "inspection_date": parse_date(field(record, "inspectiondate")),
        "approved_date": parse_date(field(record, "approveddate")),
        "original_certify_by_date": parse_date(field(record, "originalcertifybydate")),
        "original_correct_by_date": parse_date(field(record, "originalcorrectbydate")),
        "new_certify_by_date": parse_date(field(record, "newcertifybydate")),
        "new_correct_by_date": parse_date(field(record, "newcorrectbydate")),
        "certified_date": parse_date(field(record, "certifieddate")),
        "order_number": field(record, "ordernumber"),
        "nov_id": field(record, "novid", "nov_id"),
        "nov_description": field(record, "novdescription", "nov_description"),
        "current_status": field(record, "currentstatus", "current_status"),
        "current_status_date": parse_date(field(record, "currentstatusdate")),
        "raw_record": record,
    }


def bulk_upsert_hpd_violations(
    db: DbSession,
    source_version: SourceVersion,
    rows: list[dict[str, Any]],
) -> tuple[SourceVersion, int, int, int]:
    """Use set-based upserts instead of one remote query for each violation."""
    created = 0
    updated = 0
    for batch in batched(rows, HPD_UPSERT_BATCH_SIZE):
        external_ids = [row["external_id"] for row in batch]
        existing_ids = set(
            db.scalars(
                select(HpdViolation.external_id).where(
                    HpdViolation.external_id.in_(external_ids)
                )
            )
        )
        created += len(external_ids) - len(existing_ids)
        updated += len(existing_ids)
        db.execute(postgres_upsert_statement(batch))
    db.commit()
    return source_version, created, updated, 0


def batched(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def postgres_upsert_statement(batch: list[dict[str, Any]]):
    statement = postgresql_insert(HpdViolation).values(batch)
    update_values = {
        column.name: statement.excluded[column.name]
        for column in HpdViolation.__table__.columns
        if column.name not in {"id", "external_id", "created_at", "updated_at"}
    }
    return statement.on_conflict_do_update(
        index_elements=[HpdViolation.external_id],
        set_=update_values,
    )


@dataclass(frozen=True)
class HpdSnapshotManifest:
    run_mode: str
    snapshot_timestamp: str
    query_parameters: list[dict[str, str]]
    page_uris: list[str]
    page_checksums: list[str]

    def bytes(self) -> bytes:
        return json.dumps(self.__dict__, sort_keys=True).encode("utf-8")


def manifest_from_artifact(uri: str | None) -> HpdSnapshotManifest | None:
    if not uri:
        return None
    try:
        payload = json.loads(read_artifact(uri).decode("utf-8"))
        return HpdSnapshotManifest(
            run_mode=payload["run_mode"],
            snapshot_timestamp=payload["snapshot_timestamp"],
            query_parameters=list(payload.get("query_parameters", [])),
            page_uris=list(payload.get("page_uris", [])),
            page_checksums=list(payload.get("page_checksums", [])),
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def persist_manifest(
    source: Source,
    checkpoint: HpdIngestionCheckpoint,
    manifest: HpdSnapshotManifest,
) -> str:
    content = manifest.bytes()
    uri = write_artifact(source.slug, hash_bytes(content), content, "json")
    checkpoint.artifact_manifest_uri = uri
    return uri


def socrata_page_url(
    source_url: str,
    run_mode: str,
    last_page_key: dict[str, str] | None,
    status_date_watermark: date | None,
    page_size: int = HPD_PAGE_SIZE,
) -> str:
    params: dict[str, str | int] = {"$limit": page_size}
    if run_mode == "full":
        params["$order"] = "violationid ASC"
        if last_page_key and last_page_key.get("violation_id"):
            params["$where"] = (
                "violationid > '"
                + last_page_key["violation_id"].replace("'", "''")
                + "'"
            )
    elif run_mode == "delta":
        if status_date_watermark is None:
            raise ValueError("A nightly HPD delta requires a completed watermark.")
        params["$order"] = "currentstatusdate ASC, violationid ASC"
        start = status_date_watermark.isoformat()
        if last_page_key and last_page_key.get("status_date"):
            key_date = last_page_key["status_date"]
            key_id = last_page_key.get("violation_id", "").replace("'", "''")
            params["$where"] = (
                f"currentstatusdate > '{key_date}' OR "
                f"(currentstatusdate = '{key_date}' AND violationid > '{key_id}')"
            )
        else:
            params["$where"] = f"currentstatusdate >= '{start}'"
    else:
        raise ValueError(f"Unknown HPD run mode: {run_mode}")
    return f"{source_url}?{urlencode(params)}"


def page_key_for_records(
    records: list[dict[str, Any]], run_mode: str
) -> dict[str, str]:
    last = records[-1]
    violation_id = external_id_for_record(last)
    if run_mode == "full":
        return {"violation_id": violation_id}
    status_date = field(last, "currentstatusdate")
    if not status_date:
        raise ValueError(
            "Delta page is missing currentstatusdate for keyset pagination."
        )
    return {"status_date": status_date[:10], "violation_id": violation_id}


def checkpoint_for_source(
    db: DbSession, source: Source
) -> HpdIngestionCheckpoint | None:
    return db.scalar(
        select(HpdIngestionCheckpoint).where(
            HpdIngestionCheckpoint.source_id == source.id
        )
    )


def load_hpd_violations_snapshot(
    db: DbSession,
    source: Source,
    *,
    requested_mode: str = "auto",
    page_size: int = HPD_PAGE_SIZE,
    fetch_page=download_url,
) -> tuple[SourceVersion, int, int, int]:
    """Load a full snapshot or resumable 14-day overlapping delta from Socrata."""
    checkpoint = checkpoint_for_source(db, source)
    if requested_mode not in {"auto", "full", "delta"}:
        raise ValueError("HPD run mode must be auto, full, or delta.")
    if requested_mode == "auto":
        run_mode = "delta" if checkpoint and checkpoint.is_complete else "full"
    else:
        run_mode = requested_mode
    if run_mode == "delta" and (not checkpoint or not checkpoint.status_date_watermark):
        run_mode = "full"

    resuming = bool(
        checkpoint and not checkpoint.is_complete and checkpoint.run_mode == run_mode
    )
    if not resuming:
        if checkpoint is None:
            checkpoint = HpdIngestionCheckpoint(
                source_id=source.id,
                run_mode=run_mode,
                last_page_key=None,
                is_complete=False,
            )
            db.add(checkpoint)
        else:
            checkpoint.run_mode = run_mode
            checkpoint.last_page_key = None
            checkpoint.artifact_manifest_uri = None
            checkpoint.source_version_id = None
            checkpoint.is_complete = False
        db.commit()

    assert checkpoint is not None
    query_watermark = checkpoint.status_date_watermark
    if run_mode == "delta" and checkpoint.last_page_key is None:
        # Keep the completed watermark intact until this whole run succeeds.
        query_watermark = query_watermark - timedelta(days=HPD_DELTA_OVERLAP_DAYS)

    manifest = manifest_from_artifact(checkpoint.artifact_manifest_uri)
    if manifest is None or manifest.run_mode != run_mode:
        manifest = HpdSnapshotManifest(
            run_mode=run_mode,
            snapshot_timestamp=datetime.now(UTC).isoformat(),
            query_parameters=[],
            page_uris=[],
            page_checksums=[],
        )
    if checkpoint.source_version_id:
        source_version = db.get(SourceVersion, checkpoint.source_version_id)
    else:
        source_version = None
    if source_version is None:
        provisional = manifest.bytes()
        artifact = DownloadedArtifact(
            content=provisional,
            content_hash=hash_bytes(provisional),
            content_type="application/json",
            byte_size=len(provisional),
            extension="json",
            source_url=source.source_url,
        )
        source_version = create_or_get_source_version(db, source, artifact)
        checkpoint.source_version_id = source_version.id
        checkpoint.artifact_manifest_uri = source_version.artifact_uri
        db.commit()

    created = updated = skipped = 0
    max_status_date = checkpoint.status_date_watermark
    while True:
        page_url = socrata_page_url(
            source.source_url,
            run_mode,
            checkpoint.last_page_key,
            query_watermark,
            page_size,
        )
        artifact = fetch_page(page_url)
        records = json.loads(artifact.content.decode("utf-8"))
        if not isinstance(records, list):
            raise ValueError("HPD Socrata response was not a JSON array.")
        if not records:
            break
        page_uri = write_artifact(
            source.slug, artifact.content_hash, artifact.content, "json"
        )
        manifest.query_parameters.append({"url": page_url})
        manifest.page_uris.append(page_uri)
        manifest.page_checksums.append(artifact.content_hash)
        _, page_created, page_updated, page_skipped = upsert_hpd_violations(
            db, source, records, source_version
        )
        created += page_created
        updated += page_updated
        skipped += page_skipped
        for record in records:
            record_date = parse_date(field(record, "currentstatusdate"))
            if record_date and (
                max_status_date is None or record_date > max_status_date
            ):
                max_status_date = record_date
        checkpoint.last_page_key = page_key_for_records(records, run_mode)
        persist_manifest(source, checkpoint, manifest)
        db.commit()

    final_manifest = manifest.bytes()
    manifest_hash = hash_bytes(final_manifest)
    manifest_uri = write_artifact(source.slug, manifest_hash, final_manifest, "json")
    source_version.content_hash = manifest_hash
    source_version.artifact_uri = manifest_uri
    source_version.content_type = "application/json"
    source_version.byte_size = len(final_manifest)
    checkpoint.artifact_manifest_uri = manifest_uri
    checkpoint.last_page_key = None
    source_max_status_date = db.scalar(
        select(func.max(HpdViolation.current_status_date)).where(
            HpdViolation.source_id == source.id
        )
    )
    checkpoint.status_date_watermark = source_max_status_date or max_status_date
    checkpoint.is_complete = True
    db.commit()
    return source_version, created, updated, skipped
