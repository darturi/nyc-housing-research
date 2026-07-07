import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.ingestion.hpd_violations import upsert_hpd_violations
from app.ingestion.registry import seed_sources
from app.main import app
from app.models.hpd_violation import HpdViolation
from app.models.source import Source
from tests.retrieval_fixtures import TEST_PASSWORD, create_test_user


def test_hpd_violation_search_requires_authentication():
    response = TestClient(app).post(
        "/hpd/violations/search",
        json={"building_id": "2001"},
    )

    assert response.status_code == 401


def test_hpd_violation_search_returns_property_matches():
    _load_hpd_fixture()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/hpd/violations/search",
        json={"house_number": "123", "street_name": "MAIN STREET"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["external_id"] == "1001"
    assert body["results"][0]["building_id"] == "2001"


def test_hpd_violation_search_matches_combined_house_number_suffix():
    _load_hpd_fixture(
        extra_records=[
            {
                "violationid": "1003",
                "buildingid": "2003",
                "registrationid": "3003",
                "boro": "BROOKLYN",
                "housenumber": "22 FRONT",
                "streetname": "STAGG STREET",
                "zip": "11206",
                "class": "C",
                "inspectiondate": "2026-01-04T00:00:00.000",
                "novdescription": "Heat defect",
                "currentstatus": "Open",
            }
        ]
    )
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/hpd/violations/search",
        json={"house_number": "22", "street_name": "FRONT STAGG STREET"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"][0]["external_id"] == "1003"


def test_hpd_violation_response_cleans_description_without_mutating_raw_record():
    _load_hpd_fixture(
        extra_records=[
            {
                "violationid": "1004",
                "buildingid": "2004",
                "registrationid": "3004",
                "boro": "BROOKLYN",
                "housenumber": "99",
                "streetname": "MOJIBAKE STREET",
                "zip": "11206",
                "class": "A",
                "inspectiondate": "2026-01-05T00:00:00.000",
                "novdescription": "SEE HPDâS WEBSITE, WWW.NYC.GOV\\HPD.",
                "currentstatus": "Open",
            }
        ]
    )
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/hpd/violations/search",
        json={"house_number": "99", "street_name": "MOJIBAKE STREET"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["nov_description"] == (
        "SEE HPD's WEBSITE, WWW.NYC.GOV/HPD."
    )
    with SessionLocal() as db:
        raw = db.query(HpdViolation).filter_by(external_id="1004").one()
        assert raw.nov_description == "SEE HPDâS WEBSITE, WWW.NYC.GOV\\HPD."


def test_hpd_violation_search_requires_property_filter():
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/hpd/violations/search",
        json={"current_status": "Open"},
    )

    assert response.status_code == 422


def _load_hpd_fixture(extra_records: list[dict] | None = None) -> None:
    records = json.loads(Path("tests/fixtures/hpd_violations_sample.json").read_text())
    records.extend(extra_records or [])
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-violations").one()
        upsert_hpd_violations(db, source, records)


def _authenticated_client() -> TestClient:
    client = TestClient(app)
    login = client.post(
        "/auth/login",
        json={"email": "admin@example.com", "password": TEST_PASSWORD},
    )
    assert login.status_code == 200
    return client
