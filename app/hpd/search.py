from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.orm import Session as DbSession

from app.models.hpd_violation import HpdViolation
from app.schemas.hpd import (
    HpdViolationResponse,
    HpdViolationSearchRequest,
    HpdViolationSearchResponse,
)

MOJIBAKE_REPLACEMENTS = {
    "âS": "'s",
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€": '"',
    "â€“": "-",
    "â€”": "-",
    "Â§": "§",
    "Â": "",
}


def search_hpd_violations(
    db: DbSession,
    payload: HpdViolationSearchRequest,
) -> HpdViolationSearchResponse:
    query = hpd_query(payload)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(HpdViolation.inspection_date.desc(), HpdViolation.id.asc())
        .offset(payload.offset)
        .limit(payload.limit)
    ).all()
    return HpdViolationSearchResponse(
        count=total,
        results=[hpd_violation_response(row) for row in rows],
    )


def hpd_query(payload: HpdViolationSearchRequest) -> Select:
    query = select(HpdViolation)
    if payload.building_id:
        query = query.where(HpdViolation.building_id == payload.building_id)
    if payload.registration_id:
        query = query.where(HpdViolation.registration_id == payload.registration_id)
    if payload.house_number and payload.street_name:
        query = query.where(address_matches(payload))
    elif payload.house_number:
        query = query.where(HpdViolation.house_number == payload.house_number)
    elif payload.street_name:
        query = query.where(HpdViolation.street_name.ilike(f"%{payload.street_name}%"))
    if payload.zip_code:
        query = query.where(HpdViolation.zip_code == payload.zip_code)
    if payload.boro:
        query = query.where(HpdViolation.boro.ilike(payload.boro))
    if payload.violation_class:
        query = query.where(HpdViolation.violation_class.ilike(payload.violation_class))
    if payload.current_status:
        query = query.where(HpdViolation.current_status.ilike(payload.current_status))
    return query


def address_matches(payload: HpdViolationSearchRequest):
    exact_split_match = and_(
        HpdViolation.house_number == payload.house_number,
        HpdViolation.street_name.ilike(f"%{payload.street_name}%"),
    )
    full_address = (
        func.coalesce(HpdViolation.house_number, "")
        + literal(" ")
        + func.coalesce(HpdViolation.street_name, "")
    )
    requested_full_address = f"{payload.house_number} {payload.street_name}"
    return or_(
        exact_split_match,
        full_address.ilike(f"%{requested_full_address}%"),
    )


def hpd_violation_response(row: HpdViolation) -> HpdViolationResponse:
    return HpdViolationResponse(
        id=row.id,
        external_id=row.external_id,
        building_id=row.building_id,
        registration_id=row.registration_id,
        boro=row.boro,
        house_number=row.house_number,
        street_name=row.street_name,
        zip_code=row.zip_code,
        apartment=row.apartment,
        violation_class=row.violation_class,
        inspection_date=row.inspection_date,
        approved_date=row.approved_date,
        certified_date=row.certified_date,
        order_number=row.order_number,
        nov_id=row.nov_id,
        nov_description=clean_hpd_text(row.nov_description),
        current_status=row.current_status,
        current_status_date=row.current_status_date,
        source_id=row.source_id,
        source_version_id=row.source_version_id,
    )


def clean_hpd_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value
    for bad, replacement in MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, replacement)
    cleaned = cleaned.replace("WWW.NYC.GOV\\HPD", "WWW.NYC.GOV/HPD")
    return " ".join(cleaned.split())
