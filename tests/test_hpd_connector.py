import httpx
import pytest

from app.hpd.connector import HpdSocrataConnector, PropertyConnectorError, PropertyQuery
from app.workspace.network import NetworkAccessDenied, NetworkPolicy


def _row(
    violation_id="100",
    *,
    building_id="375411",
    registration_id="306067",
    house="22 FRONT",
    street="STAGG STREET",
    inspection="2026-09-01T00:00:00.000",
):
    return {
        "violationid": violation_id,
        "buildingid": building_id,
        "registrationid": registration_id,
        "boro": "BROOKLYN",
        "housenumber": house,
        "streetname": street,
        "zip": "11206",
        "class": "C",
        "inspectiondate": inspection,
        "novdescription": "Fixture violation",
        "currentstatus": "VIOLATION OPEN",
        "violationstatus": "Open",
    }


def test_bounded_typed_request_projection_filters_and_token() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["token"] = request.headers.get("X-App-Token")
        return httpx.Response(
            200,
            json=[_row()],
            headers={"Last-Modified": "Mon, 14 Sep 2026 12:00:00 GMT"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = HpdSocrataConnector(
        NetworkPolicy(offline=False), app_token="fixture-token", client=client
    )
    try:
        response = connector.search(
            PropertyQuery(
                house_number="22 front",
                street_name="Stagg St",
                borough="BK",
                violation_class="c",
                status="open",
                inspection_date_from="2026-08-01",
                inspection_date_to="2026-09-01",
            )
        )
    finally:
        client.close()

    assert response.returned_count == 1
    assert response.dataset_id == "wvxf-dwi5"
    assert response.records[0].violation_id == "100"
    assert captured["token"] == "fixture-token"
    assert "%24limit=51" in captured["url"]
    assert "upper%28streetname%29" in captured["url"]
    assert "STAGG+STREET" in captured["url"]
    assert "2026-08-01T00%3A00%3A00.000" in captured["url"]
    assert "2026-09-02T00%3A00%3A00.000" in captured["url"]
    assert response.total_count is None
    assert response.has_more is False
    assert response.next_cursor is None
    assert response.fetch_started_at <= response.fetch_completed_at
    assert response.source_update_time.isoformat() == "2026-09-14T12:00:00+00:00"


def test_address_and_registration_ambiguity_returns_candidates() -> None:
    rows = [
        _row("100", building_id="1", registration_id="9"),
        _row("101", building_id="2", registration_id="9"),
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=rows))
    )
    try:
        result = HpdSocrataConnector(
            NetworkPolicy(offline=False), client=client
        ).search(PropertyQuery(registration_id="9"))
    finally:
        client.close()
    assert result.requires_selection is True
    assert result.records == ()
    assert {candidate.building_id for candidate in result.candidates} == {"1", "2"}
    assert result.is_complete is False


def test_verified_zero_does_not_claim_building_nonexistence() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
    )
    try:
        result = HpdSocrataConnector(
            NetworkPolicy(offline=False), client=client
        ).search(PropertyQuery(building_id="375411"))
    finally:
        client.close()
    assert result.source_status == "verified_zero"
    assert result.returned_count == 0
    assert result.is_complete is True


def test_continuation_is_request_bound_and_not_a_remote_url() -> None:
    rows = [
        _row(str(index), inspection=f"2026-09-{30 - index:02d}T00:00:00.000")
        for index in range(1, 4)
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=rows))
    )
    connector = HpdSocrataConnector(NetworkPolicy(offline=False), client=client)
    try:
        first = connector.search(PropertyQuery(building_id="375411", limit=2))
        assert first.continuation and not first.continuation.startswith("http")
        with pytest.raises(ValueError, match="does not match"):
            connector.search(
                PropertyQuery(
                    building_id="999999",
                    limit=2,
                    continuation=first.continuation,
                )
            )
    finally:
        client.close()


def test_invalid_fields_and_schema_errors_fail_closed() -> None:
    PropertyQuery(
        house_number="35-20A",
        street_name="Queens Blvd.",
        borough="QN",
    ).validate()
    with pytest.raises(ValueError, match="unsupported"):
        PropertyQuery(house_number="22; DROP", street_name="STAGG ST").validate()
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        PropertyQuery(building_id="1", inspection_date_from="09/01/2026").validate()
    with pytest.raises(ValueError, match="on or before"):
        PropertyQuery(
            building_id="1",
            inspection_date_from="2026-09-02",
            inspection_date_to="2026-09-01",
        ).validate()

    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[{"buildingid": "1"}])
        )
    )
    try:
        with pytest.raises(PropertyConnectorError, match="identity"):
            HpdSocrataConnector(NetworkPolicy(offline=False), client=client).search(
                PropertyQuery(building_id="1")
            )
    finally:
        client.close()


def test_offline_blocks_before_transport() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(NetworkAccessDenied):
            HpdSocrataConnector(NetworkPolicy(offline=True), client=client).search(
                PropertyQuery(building_id="1")
            )
        assert called is False
    finally:
        client.close()
