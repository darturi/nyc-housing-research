import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.models.hpd_violation import HpdViolation
from app.models.source import Source
from app.models.source_version import SourceVersion


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
    if source_version is None:
        source_version = source_version_for_records(db, source, records)

    created = 0
    updated = 0
    skipped = 0
    limit = get_settings().hpd_violations_limit
    for record in records[:limit]:
        external_id = external_id_for_record(record)
        existing = db.scalar(
            select(HpdViolation).where(HpdViolation.external_id == external_id)
        )
        values = {
            "source_id": source.id,
            "source_version_id": source_version.id,
            "external_id": external_id,
            "building_id": field(record, "buildingid", "building_id"),
            "registration_id": field(record, "registrationid", "registration_id"),
            "boro": field(record, "boro", "borough"),
            "house_number": field(record, "housenumber", "house_number"),
            "street_name": field(record, "streetname", "street_name"),
            "zip_code": field(record, "zip", "zip_code"),
            "apartment": field(record, "apartment"),
            "violation_class": field(record, "class", "violationclass"),
            "inspection_date": parse_date(field(record, "inspectiondate")),
            "approved_date": parse_date(field(record, "approveddate")),
            "original_certify_by_date": parse_date(
                field(record, "originalcertifybydate")
            ),
            "original_correct_by_date": parse_date(
                field(record, "originalcorrectbydate")
            ),
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
        if existing is None:
            db.add(HpdViolation(**values))
            created += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)
            updated += 1
    db.commit()
    return source_version, created, updated, skipped
