import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.corpus.service import CorpusService, SourceArtifact
from app.hpd.connector import HpdViolationRecord, PropertySearchResponse
from app.local_app import create_local_app
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext


class FixtureConnector:
    manifest = {"connector_version": "hpd-soda21-v1"}

    def __init__(self):
        self.calls = 0

    def search(self, query, *, deadline=None):
        self.calls += 1
        row = HpdViolationRecord(
            violation_id="100",
            building_id=query.building_id,
            registration_id="306067",
            borough="BROOKLYN",
            house_number="22 FRONT",
            street_name="STAGG STREET",
            zip_code="11206",
            apartment="1F",
            violation_class="C",
            inspection_date="2026-09-01T00:00:00.000",
            approved_date=None,
            certified_date=None,
            order_number="501",
            nov_id="900",
            description="Repair the fixture condition.",
            current_status="VIOLATION OPEN",
            current_status_date="2026-09-02T00:00:00.000",
            violation_status="Open",
        )
        return PropertySearchResponse(
            query=query,
            candidates=(),
            records=(row,),
            requires_selection=False,
            continuation=None,
            is_complete=True,
            returned_count=1,
            fetched_at=datetime(2026, 9, 14, tzinfo=UTC),
            dataset_id="wvxf-dwi5",
            dataset_url="https://data.cityofnewyork.us/example",
            connector_version="hpd-soda21-v1",
            source_status="success",
            total_count=None,
            has_more=False,
            fetch_started_at=datetime(2026, 9, 14, 12, tzinfo=UTC),
            fetch_completed_at=datetime(2026, 9, 14, 12, 0, 1, tzinfo=UTC),
        )

    def close(self):
        return None


def _app(tmp_path):
    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    CorpusService(storage).install_artifacts(
        [
            SourceArtifact(
                slug="ny-rpapl",
                content=b"\xc2\xa7 711. Housing maintenance summary proceedings.",
                source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
                content_type="text/plain",
                retrieved_at=datetime(2026, 9, 14, tzinfo=UTC),
            )
        ],
        allow_partial=True,
    )
    storage.close()
    connectors = []

    def factory(_context, _token):
        connector = FixtureConnector()
        connectors.append(connector)
        return connector

    return (
        context,
        create_local_app(context, property_connector_factory=factory),
        connectors,
    )


def _auth(client, app):
    response = client.post(
        "/api/v1/session/exchange",
        headers={"Origin": "http://127.0.0.1"},
        json={"launch_token": app.state.launch_token},
    )
    return {
        "Origin": "http://127.0.0.1",
        "X-CSRF-Token": response.json()["csrf_token"],
    }


def test_property_search_cache_and_fixed_evidence_summary(tmp_path) -> None:
    _context, app, connectors = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = _auth(client, app)
        request = {
            "building_id": "375411",
            "inspection_date_from": "2026-01-01",
            "inspection_date_to": "2026-12-31",
            "limit": 50,
        }
        first = client.post("/api/v1/properties/search", headers=headers, json=request)
        second = client.post("/api/v1/properties/search", headers=headers, json=request)
        assert first.status_code == 200
        assert first.json()["mode"] == "property"
        assert first.json()["coverage"]["returned_count"] == 1
        assert first.json()["total_count"] is None
        assert first.json()["has_more"] is False
        assert first.json()["next_cursor"] is None
        assert first.json()["provenance"]["fetch_started_at"].startswith(
            "2026-09-14T12:00:00"
        )
        assert first.json()["provenance"]["dataset_id"] == "wvxf-dwi5"
        assert first.json()["records"][0]["violation_id"] == "100"
        assert second.json()["cache_status"] == "fresh_cache"
        assert sum(connector.calls for connector in connectors) == 1

        refreshed = client.post(
            "/api/v1/properties/search",
            headers=headers,
            json={**request, "refresh": True},
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["cache_status"] == "live"
        assert sum(connector.calls for connector in connectors) == 2

        summary = client.post(
            "/api/v1/properties/summarize", headers=headers, json=request
        )
        assert summary.status_code == 200
        assert summary.json()["status"] == "synthetic_demo"
        assert "Synthetic provider output" in summary.json()["summary"]
        assert summary.json()["property_evidence_ids"] == ["100"]
        assert summary.json()["property_filters"]["inspection_date_from"] == (
            "2026-01-01"
        )
        assert summary.json()["property_fetch_started_at"].startswith(
            "2026-09-14T12:00:00"
        )
        assert summary.json()["prompt_version"] == "property-summary-v1"
        assert "[P1]" in summary.json()["summary"]
        assert summary.json()["mode"] == "property_summary"
        assert summary.json()["coverage"]["property_evidence_count"] == 1
        assert (
            summary.json()["provenance"]["operation_id"]
            == summary.json()["operation_id"]
        )

        exported = client.post(
            "/api/v1/exports",
            headers=headers,
            json={"kind": "property", "query": request},
        )
        assert exported.status_code == 200
        assert exported.json()["status"] == "created"
        assert exported.json()["mode"] == "property_export"
        assert exported.json()["coverage"]["returned_count"] == 1
        assert client.get(exported.json()["download_url"]).status_code == 200

        complete = client.post(
            "/api/v1/exports",
            headers=headers,
            json={
                "kind": "property_complete",
                "query": request,
                "max_pages": 2,
                "deadline_seconds": 5,
            },
        )
        assert complete.status_code == 202
        assert complete.json()["mode"] == "property_complete_export"
        complete_job = None
        for _ in range(100):
            complete_job = client.get(f"/api/v1/jobs/{complete.json()['id']}").json()
            if complete_job["state"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.01)
        assert complete_job is not None
        assert complete_job["state"] == "succeeded"
        assert complete_job["resume"]["is_complete"] is True
        filename = complete_job["resume"]["output_filename"]
        assert client.get(f"/api/v1/exports/{filename}").status_code == 200


def test_property_routes_reject_arbitrary_query_fields_and_missing_csrf(
    tmp_path,
) -> None:
    _context, app, _connectors = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        headers = _auth(client, app)
        bad = client.post(
            "/api/v1/properties/search",
            headers=headers,
            json={"building_id": "1", "where": "1=1"},
        )
        assert bad.status_code == 400
        no_csrf = client.post(
            "/api/v1/properties/search",
            headers={"Origin": "http://127.0.0.1"},
            json={"building_id": "1"},
        )
        assert no_csrf.status_code == 401
