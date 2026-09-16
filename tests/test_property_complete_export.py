import csv
from datetime import UTC, datetime
from pathlib import Path

import httpx
from sqlalchemy import select

from app.exporting.service import ResearchExporter
from app.hpd.cache import CachedPropertyRepository
from app.hpd.complete import export_complete_property_result
from app.hpd.connector import (
    HpdSocrataConnector,
    HpdViolationRecord,
    PropertyConnectorError,
    PropertyQuery,
    PropertySearchResponse,
)
from app.jobs.property_exports import PropertyExportJobs
from app.jobs.runtime import Deadline
from app.jobs.service import JobService
from app.storage.database import LocalStorage
from app.storage.schema import property_cache
from app.workspace.context import WorkspaceContext


def _row(violation_id: str, inspection: str) -> dict[str, str]:
    return {
        "violationid": violation_id,
        "buildingid": "42",
        "registrationid": "7",
        "boro": "BROOKLYN",
        "housenumber": "10",
        "streetname": "COURT STREET",
        "zip": "11201",
        "class": "B",
        "inspectiondate": inspection,
        "novdescription": "Fixture",
        "violationstatus": "Open",
    }


def test_complete_export_pages_tracks_job_and_releases_cache_pins(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                json=[
                    _row("100", "2026-09-03T00:00:00.000"),
                    _row("101", "2026-09-02T00:00:00.000"),
                    _row("102", "2026-09-01T00:00:00.000"),
                ],
            )
        return httpx.Response(200, json=[_row("102", "2026-09-01T00:00:00.000")])

    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    connector = HpdSocrataConnector(context.network, client=client)
    repository = CachedPropertyRepository(storage, connector)
    try:
        result = export_complete_property_result(
            repository,
            ResearchExporter(context.paths),
            JobService(storage.state_engine),
            PropertyQuery(building_id="42", limit=2),
            deadline=Deadline.after(5),
        )
        assert result.is_complete is True
        assert result.page_count == 2
        assert result.row_count == 3
        with Path(result.path).open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        assert len(rows) == 4
        assert "request_scope_json" in rows[0]
        with storage.state_engine.connect() as connection:
            assert not any(connection.scalars(select(property_cache.c.pinned)))
        finished = JobService(storage.state_engine).get(result.job_id)
        assert finished.state.value == "succeeded"
    finally:
        client.close()
        storage.close()


def test_durable_property_export_resumes_under_the_same_job_id(tmp_path) -> None:
    attempts = 0

    class FlakyConnector:
        manifest = {"connector_version": "hpd-soda21-v1"}

        def search(self, query, *, deadline=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PropertyConnectorError("temporary fixture outage")
            record = HpdViolationRecord(
                violation_id="100",
                building_id="42",
                registration_id="7",
                borough="BROOKLYN",
                house_number="10",
                street_name="COURT STREET",
                zip_code="11201",
                apartment=None,
                violation_class="B",
                inspection_date="2026-09-03T00:00:00.000",
                approved_date=None,
                certified_date=None,
                order_number=None,
                nov_id=None,
                description="Fixture",
                current_status="VIOLATION OPEN",
                current_status_date=None,
                violation_status="Open",
            )
            return PropertySearchResponse(
                query=query,
                candidates=(),
                records=(record,),
                requires_selection=False,
                continuation=None,
                is_complete=True,
                returned_count=1,
                fetched_at=datetime(2026, 9, 14, tzinfo=UTC),
                dataset_id="wvxf-dwi5",
                dataset_url="https://data.cityofnewyork.us/example",
                connector_version="hpd-soda21-v1",
                source_status="success",
            )

        def close(self):
            return None

    context = WorkspaceContext.from_options(
        tmp_path / "workspace", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    runner = PropertyExportJobs(
        context,
        storage,
        connector_factory=lambda _context, _credential: FlakyConnector(),
    )
    try:
        created = runner.submit(
            PropertyQuery(building_id="42", limit=2),
            max_pages=2,
            deadline_seconds=5,
        )
        failed = runner.wait(created.id)
        assert failed.state.value == "failed"
        assert failed.retryable is True

        resumed = runner.resume(created.id)
        assert resumed.id == created.id
        completed = runner.wait(created.id)
        assert completed.state.value == "succeeded"
        assert completed.resume["output_filename"].endswith(".csv")
        assert attempts == 2
    finally:
        runner.close()
        storage.close()
