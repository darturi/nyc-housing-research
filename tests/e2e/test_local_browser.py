from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime

import pytest
import uvicorn

from app.corpus.service import SourceArtifact
from app.hpd.connector import (
    BuildingCandidate,
    HpdViolationRecord,
    PropertySearchResponse,
)
from app.jobs.service import JobService
from app.local_app import create_local_app
from app.storage.database import LocalStorage
from app.workspace.context import WorkspaceContext

pytestmark = pytest.mark.skipif(
    os.environ.get("NYC_HOUSING_BROWSER_E2E") != "1",
    reason="set NYC_HOUSING_BROWSER_E2E=1 after installing a Playwright browser",
)


class FixturePropertyConnector:
    manifest = {"connector_version": "hpd-soda21-v2"}

    def search(self, query, *, deadline=None):
        started = datetime.now(UTC)
        if not query.building_id:
            return PropertySearchResponse(
                query=query,
                candidates=(
                    BuildingCandidate(
                        "42", "7", "BROOKLYN", "10", "COURT STREET", "11201"
                    ),
                    BuildingCandidate(
                        "43", "8", "BROOKLYN", "10", "COURT STREET", "11201"
                    ),
                ),
                records=(),
                requires_selection=True,
                continuation=None,
                is_complete=False,
                returned_count=0,
                fetched_at=datetime.now(UTC),
                dataset_id="wvxf-dwi5",
                dataset_url="https://data.cityofnewyork.us/example",
                connector_version="hpd-soda21-v2",
                source_status="success",
                fetch_started_at=started,
                fetch_completed_at=datetime.now(UTC),
            )
        page_two = bool(query.continuation)
        record = _record("102" if page_two else "101", query.building_id)
        cursor = None if page_two else "fixture-next-page"
        return PropertySearchResponse(
            query=query,
            candidates=(),
            records=(record,),
            requires_selection=False,
            continuation=cursor,
            is_complete=page_two,
            returned_count=1,
            fetched_at=datetime.now(UTC),
            dataset_id="wvxf-dwi5",
            dataset_url="https://data.cityofnewyork.us/example",
            connector_version="hpd-soda21-v2",
            source_status="success",
            has_more=not page_two,
            next_cursor=cursor,
            fetch_started_at=started,
            fetch_completed_at=datetime.now(UTC),
        )

    def close(self):
        return None


def _record(violation_id: str, building_id: str) -> HpdViolationRecord:
    return HpdViolationRecord(
        violation_id=violation_id,
        building_id=building_id,
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
        description="Fixture condition requiring repair.",
        current_status="VIOLATION OPEN",
        current_status_date=None,
        violation_status="Open",
    )


def _artifact(text: str) -> SourceArtifact:
    return SourceArtifact(
        slug="ny-rpapl",
        content=text.encode(),
        source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
        content_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )


def test_complete_local_browser_journey(tmp_path, monkeypatch) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    context = WorkspaceContext.from_options(
        tmp_path / "Browser journey ü", environment={}, initialize=True
    )
    storage = LocalStorage.open(context.paths, initialize=True)
    long_evidence = " ".join(["Landlord tenant relationship evidence."] * 45)
    jobs = JobService(storage.state_engine)
    retry_job = jobs.create(
        "corpus_update",
        "corpus:ny-rpapl",
        resume={"operation": "update", "source": "ny-rpapl", "allow_partial": True},
    )
    jobs.claim(retry_job.id, "failed-fixture")
    jobs.fail(
        retry_job.id,
        "failed-fixture",
        error_code="fixture_failure",
        error_message="Temporary fixture source failure.",
        retryable=True,
    )
    storage.close()

    def downloader(_context, slugs, *, progress):
        assert slugs == ["ny-rpapl"]
        progress("Downloading source 1/1: ny-rpapl")
        return [
            _artifact(f"§ 711. Grounds for summary proceedings\n{long_evidence}")
        ]

    monkeypatch.setattr("app.jobs.maintenance.download_source_artifacts", downloader)
    app = create_local_app(
        context,
        property_connector_factory=lambda _context, _token: FixturePropertyConnector(),
    )
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started and server.servers:
            break
        time.sleep(0.01)
    assert server.started and server.servers
    port = next(iter(server.servers)).sockets[0].getsockname()[1]

    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    launch_options = {"headless": True}
    if executable:
        launch_options["executable_path"] = executable
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(**launch_options)
            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{port}/#launch={app.state.launch_token}",
                wait_until="networkidle",
            )
            page.locator("#application").wait_for(state="visible")

            page.get_by_text("Finish first setup").wait_for()
            source = page.locator("#source-list li").filter(
                has_text="New York Real Property Actions and Proceedings Law"
            )
            page.once("dialog", lambda dialog: dialog.accept())
            source.get_by_role("button", name="Install this module").click()
            page.locator("#job-list").get_by_text(
                "corpus update · succeeded", exact=False
            ).wait_for()

            page.locator('[data-view="research"]').click()
            page.select_option("#research-mode", "answer")
            page.fill("#question", "What does RPAPL section 711 concern?")
            page.locator("#research-form button[type=submit]").click()
            page.locator("#research-result").get_by_text(
                "Synthetic provider output", exact=False
            ).wait_for()
            page.get_by_role("button", name="Show full excerpt").click()
            page.get_by_role("button", name="Show less").wait_for()
            page.get_by_role("link", name="Open official source").wait_for()

            page.locator('[data-view="settings"]').click()
            page.fill("#monthly-budget", "12.25")
            page.select_option("#answer-profile", "openai-answer-luna-v1")
            page.select_option(
                "#embedding-profile", "openai-embedding-3-small-v1"
            )
            page.locator("#settings-form button[type=submit]").click()
            page.get_by_text(
                "Saved. Restart before starting new model jobs."
            ).wait_for()
            fixture_credential = "sk-browser-setup-fixture-value"
            page.fill("#provider-key", fixture_credential)
            page.select_option("#credential-storage", "file")
            page.locator("#credential-form button[type=submit]").click()
            page.get_by_text(
                "Credential openai present via secret_file", exact=False
            ).wait_for()
            assert page.locator("#provider-key").input_value() == ""
            assert fixture_credential not in page.content()

            page.locator('[data-view="sources"]').click()
            page.get_by_role("button", name="Resume").click()
            page.locator("#job-list").get_by_text(
                "corpus update · succeeded", exact=False
            ).wait_for()

            page.locator('[data-view="research"]').click()
            page.fill("#house-number", "10")
            page.fill("#street-name", "Court St")
            page.select_option("#borough", "Brooklyn")
            page.get_by_role("button", name="Look up HPD records").click()
            page.get_by_text("Select the intended HPD building:").wait_for()
            page.get_by_role(
                "button", name="10 COURT STREET", exact=False
            ).first.click()
            page.get_by_text("101", exact=True).wait_for()
            page.get_by_role("button", name="Next page").click()
            page.get_by_text("102", exact=True).wait_for()
            page.get_by_role("button", name="Refresh from HPD").click()
            page.get_by_text("Open official HPD dataset").wait_for()

            page.select_option("#research-mode", "auto")
            page.fill(
                "#question",
                "What does Housing Maintenance Code section 27-2005 require "
                "for building ID 42?",
            )
            page.locator("#research-form button[type=submit]").click()
            page.locator("#research-result h3").get_by_text(
                "Generation", exact=False
            ).wait_for()
            assert "Property mode resolved" not in page.locator(
                "#research-result"
            ).inner_text()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        assert not thread.is_alive()
