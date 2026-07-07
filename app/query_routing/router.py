import re
from dataclasses import dataclass

from app.schemas.hpd import HpdViolationSearchRequest

PROPERTY_TERMS = {
    "hpd",
    "violation",
    "violations",
    "building",
    "registration",
    "apartment",
    "property",
    "address",
}

LEGAL_TERMS = {
    "admin code",
    "housing maintenance code",
    "multiple dwelling law",
    "rpapl",
    "section",
    "§",
}

BUILDING_ID_PATTERN = re.compile(r"\bbuilding(?:\s+id)?\s*[:#]?\s*(\d+)\b", re.I)
REGISTRATION_ID_PATTERN = re.compile(
    r"\bregistration(?:\s+id)?\s*[:#]?\s*(\d+)\b",
    re.I,
)
ZIP_PATTERN = re.compile(r"\b(1\d{4})\b")
ADDRESS_PATTERN = re.compile(
    r"\b(\d+[a-zA-Z]?)\s+([A-Za-z][A-Za-z0-9 .'-]*?)"
    r"(?:\s+(?:violations?|hpd|open|closed|complaints?|problems?))?\s*$",
    re.I,
)


@dataclass(frozen=True)
class QueryRoute:
    kind: str
    reason: str


def classify_query(question: str) -> QueryRoute:
    normalized = question.lower()
    has_property_signal = any(term in normalized for term in PROPERTY_TERMS)
    has_legal_signal = any(term in normalized for term in LEGAL_TERMS)
    has_identifier = bool(
        BUILDING_ID_PATTERN.search(question)
        or REGISTRATION_ID_PATTERN.search(question)
        or ADDRESS_PATTERN.search(question)
    )
    if has_property_signal and has_identifier:
        return QueryRoute("property", "property_signal_with_identifier")
    if has_property_signal and not has_legal_signal:
        return QueryRoute("property", "property_signal")
    return QueryRoute("legal", "default_legal")


def hpd_request_from_question(question: str, limit: int) -> HpdViolationSearchRequest:
    building_match = BUILDING_ID_PATTERN.search(question)
    if building_match:
        return HpdViolationSearchRequest(
            building_id=building_match.group(1),
            limit=limit,
        )

    registration_match = REGISTRATION_ID_PATTERN.search(question)
    if registration_match:
        return HpdViolationSearchRequest(
            registration_id=registration_match.group(1),
            limit=limit,
        )

    address_match = ADDRESS_PATTERN.search(question)
    if address_match:
        street_name = normalize_street_name(address_match.group(2))
        zip_match = ZIP_PATTERN.search(question)
        return HpdViolationSearchRequest(
            house_number=address_match.group(1),
            street_name=street_name,
            zip_code=zip_match.group(1) if zip_match else None,
            limit=limit,
        )

    raise ValueError(
        "Property questions need a building ID, registration ID, or address."
    )


def normalize_street_name(value: str) -> str:
    without_zip = ZIP_PATTERN.sub("", value)
    cleaned = re.sub(r"\b(?:in|at|for|nyc|new york)\b", "", without_zip, flags=re.I)
    return " ".join(cleaned.upper().split())
