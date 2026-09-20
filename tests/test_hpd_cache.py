from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.hpd.cache import CachedPropertyRepository
from app.hpd.connector import HpdSocrataConnector, PropertyQuery
from app.storage.database import LocalStorage
from app.storage.schema import property_cache
from app.workspace.network import NetworkAccessDenied, NetworkPolicy
from app.workspace.paths import resolve_workspace_paths


def _storage(tmp_path):
    paths = resolve_workspace_paths(tmp_path / "workspace", environment={})
    return LocalStorage.open(paths, initialize=True)


def _row(violation="100"):
    return {
        "violationid": violation,
        "buildingid": "375411",
        "registrationid": "306067",
        "boro": "BROOKLYN",
        "housenumber": "22 FRONT",
        "streetname": "STAGG STREET",
        "zip": "11206",
        "class": "C",
        "inspectiondate": "2026-09-01T00:00:00.000",
        "novdescription": "Fixture violation",
        "currentstatus": "VIOLATION OPEN",
        "violationstatus": "Open",
    }


def test_fresh_cache_avoids_a_second_agency_request(tmp_path) -> None:
    storage = _storage(tmp_path)
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if "inspectiondate is null" in _request.url.params["$where"]:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_row()])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = HpdSocrataConnector(NetworkPolicy(False), client=client)
    repository = CachedPropertyRepository(storage, connector)
    now = datetime(2026, 9, 14, tzinfo=UTC)
    try:
        first = repository.search(PropertyQuery(building_id="375411"), now=now)
        second = repository.search(
            PropertyQuery(building_id="375411"), now=now + timedelta(hours=1)
        )
        assert first.cache_status == "live"
        assert second.cache_status == "fresh_cache"
        assert calls == 2
    finally:
        client.close()
        storage.close()


def test_stale_success_is_available_offline_with_original_fetch_date(tmp_path) -> None:
    storage = _storage(tmp_path)
    now = datetime(2026, 9, 14, tzinfo=UTC)
    live_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[_row()])
        )
    )
    live = HpdSocrataConnector(NetworkPolicy(False), client=live_client)
    try:
        original = CachedPropertyRepository(storage, live).search(
            PropertyQuery(building_id="375411"), now=now
        )
    finally:
        live_client.close()

    offline_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: pytest.fail("offline transport must not be called")
        )
    )
    offline = HpdSocrataConnector(NetworkPolicy(True), client=offline_client)
    try:
        cached = CachedPropertyRepository(storage, offline).search(
            PropertyQuery(building_id="375411"),
            now=now + timedelta(hours=25),
        )
        assert cached.cache_status == "stale_cache"
        assert cached.stale is True
        assert cached.fetched_at == original.fetched_at
    finally:
        offline_client.close()
        storage.close()


def test_connector_upgrade_does_not_reuse_cache_that_omitted_undated_records(
    tmp_path,
) -> None:
    storage = _storage(tmp_path)
    query = PropertyQuery(building_id="375411")
    now = datetime(2026, 9, 14, tzinfo=UTC)

    def handler(request):
        row = _row("200")
        row.pop("inspectiondate")
        return httpx.Response(
            200,
            json=[row]
            if "inspectiondate is null" in request.url.params["$where"]
            else [],
        )

    try:
        with httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ) as client:
            old = HpdSocrataConnector(NetworkPolicy(False), client=client)
            old.manifest["connector_version"] = "hpd-soda21-v2"
            original = CachedPropertyRepository(storage, old).search(query, now=now)
            assert original.source_status == "verified_zero"
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            current = HpdSocrataConnector(NetworkPolicy(False), client=client)
            response = CachedPropertyRepository(storage, current).search(query, now=now)
        assert response.cache_status == "live"
        assert response.returned_count == 1
        assert response.records[0].violation_id == "200"
    finally:
        storage.close()


def test_verified_empty_expires_quickly_and_is_not_stale_fallback(tmp_path) -> None:
    storage = _storage(tmp_path)
    now = datetime(2026, 9, 14, tzinfo=UTC)
    live_client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=[]))
    )
    live = HpdSocrataConnector(NetworkPolicy(False), client=live_client)
    try:
        result = CachedPropertyRepository(storage, live).search(
            PropertyQuery(building_id="1"), now=now
        )
        assert result.source_status == "verified_zero"
    finally:
        live_client.close()

    offline_client = httpx.Client(transport=httpx.MockTransport(lambda _request: None))
    offline = HpdSocrataConnector(NetworkPolicy(True), client=offline_client)
    try:
        with pytest.raises(NetworkAccessDenied):
            CachedPropertyRepository(storage, offline).search(
                PropertyQuery(building_id="1"), now=now + timedelta(hours=2)
            )
    finally:
        offline_client.close()
        storage.close()


def test_filter_and_pagination_identity_do_not_share_cache(tmp_path) -> None:
    storage = _storage(tmp_path)
    calls = 0

    def handler(_request):
        nonlocal calls
        calls += 1
        if "inspectiondate is null" in _request.url.params["$where"]:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[_row(str(calls))])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = HpdSocrataConnector(NetworkPolicy(False), client=client)
    repository = CachedPropertyRepository(storage, connector)
    try:
        repository.search(PropertyQuery(building_id="1", status="open"))
        repository.search(PropertyQuery(building_id="1", status="closed"))
        assert calls == 4
        with storage.state_engine.connect() as connection:
            assert (
                connection.scalar(select(func.count()).select_from(property_cache)) == 2
            )
    finally:
        client.close()
        storage.close()


def test_cache_clear_removes_only_cache_artifacts(tmp_path) -> None:
    storage = _storage(tmp_path)
    keep = storage.paths.artifacts / "keep.txt"
    keep.write_text("keep", encoding="utf-8")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[_row()])
        )
    )
    connector = HpdSocrataConnector(NetworkPolicy(False), client=client)
    repository = CachedPropertyRepository(storage, connector)
    try:
        repository.search(PropertyQuery(building_id="1"))
        assert repository.clear() == 1
        assert keep.read_text(encoding="utf-8") == "keep"
    finally:
        client.close()
        storage.close()
