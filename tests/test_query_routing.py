import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.session import SessionLocal
from app.ingestion.hpd_violations import upsert_hpd_violations
from app.ingestion.registry import seed_sources
from app.main import app
from app.models.source import Source
from app.query_routing.router import classify_query
from tests.retrieval_fixtures import (
    TEST_PASSWORD,
    create_hpd_guidance_quality_corpus,
    create_retrieval_corpus,
    create_rpapl_corpus_with_guidance_noise,
    create_test_user,
)


def test_classify_query_routes_property_address():
    route = classify_query("Show HPD violations at 123 MAIN STREET")

    assert route.kind == "property"


def test_classify_query_routes_broad_hpd_guidance_to_legal():
    complaint_route = classify_query(
        "How can a tenant report a housing complaint to HPD?"
    )
    enforcement_route = classify_query(
        "What HPD enforcement information is available for tenants and owners?"
    )

    assert complaint_route.kind == "legal"
    assert enforcement_route.kind == "legal"


def test_classify_query_routes_explicit_property_lookup_without_identifier():
    route = classify_query("Show HPD violations")

    assert route.kind == "property"
    assert route.reason == "property_lookup_missing_identifier"


def test_query_endpoint_routes_property_question_to_hpd_results():
    _load_hpd_fixture()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/query",
        json={"question": "Show HPD violations at 123 MAIN STREET"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "property"
    assert body["hpd_violations"]["count"] == 1
    assert body["hpd_violations"]["results"][0]["external_id"] == "1001"


def test_query_endpoint_finds_hpd_address_with_house_number_suffix():
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
        "/query",
        json={"question": "Show HPD violations at 22 FRONT STAGG STREET", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "property"
    assert body["hpd_violations"]["count"] == 1
    assert body["hpd_violations"]["results"][0]["external_id"] == "1003"


def test_query_endpoint_answers_hpd_guidance_question():
    create_hpd_guidance_quality_corpus()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/query",
        json={"question": "How can a tenant report a housing complaint to HPD?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "legal"
    assert body["answer"]["answer_status"] == "answered"
    assert body["answer"]["citations"][0]["source_name"] == (
        "HPD Tenant and Owner Guidance"
    )


def test_query_endpoint_property_lookup_without_identifier_returns_message():
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/query",
        json={"question": "Show HPD violations"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "property"
    assert body["message"] == (
        "Property questions need a building ID, registration ID, or address."
    )


def test_query_endpoint_routes_legal_question_to_answer():
    create_retrieval_corpus()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/query",
        json={"question": "What does NYC Admin Code § 27-2005 require?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "legal"
    assert body["answer"]["answer_status"] == "answered"


def test_query_endpoint_answers_rpapl_section_phrase_from_rpapl_source():
    create_rpapl_corpus_with_guidance_noise()
    create_test_user()
    client = _authenticated_client()

    response = client.post(
        "/query",
        json={"question": "What does RPAPL section 711 cover?", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["route"] == "legal"
    assert body["answer"]["answer_status"] == "answered"
    assert body["answer"]["citations"][0]["citation"] == "RPAPL § 711"
    assert "landlord-tenant relationship" in body["answer"]["answer"]
    assert "BOOMR" not in body["answer"]["answer"]


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
