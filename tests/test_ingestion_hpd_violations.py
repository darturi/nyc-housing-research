import json
from datetime import date
from pathlib import Path

from app.db.session import SessionLocal
from app.ingestion.downloaders import DownloadedArtifact, hash_bytes
from app.ingestion.hpd_violations import (
    load_hpd_violations_snapshot,
    normalized_full_address,
    postgres_upsert_statement,
    socrata_page_url,
    upsert_hpd_violations,
)
from app.ingestion.registry import seed_sources
from app.models.hpd_ingestion_checkpoint import HpdIngestionCheckpoint
from app.models.hpd_violation import HpdViolation
from app.models.source import Source


def sample_records():
    return json.loads(Path("tests/fixtures/hpd_violations_sample.json").read_text())


def test_hpd_loader_inserts_and_updates_records(tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path)
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter(Source.slug == "hpd-violations").one()

        source_version, created, updated, skipped = upsert_hpd_violations(
            db,
            source,
            sample_records(),
        )
        changed_records = sample_records()
        changed_records[0]["currentstatus"] = "Closed"
        _, second_created, second_updated, _ = upsert_hpd_violations(
            db,
            source,
            changed_records,
            source_version,
        )

        assert created == 2
        assert updated == 0
        assert skipped == 0
        assert second_created == 0
        assert second_updated == 2
        assert db.query(HpdViolation).count() == 2
        violation = db.query(HpdViolation).filter_by(external_id="1001").one()
        assert violation.current_status == "Closed"
        assert violation.raw_record["violationid"] == "1001"
        assert violation.normalized_full_address == "123 MAINSTREET"


def test_hpd_snapshot_resumes_persists_manifest_and_applies_delta(tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path)
    records = sample_records()
    calls: list[str] = []
    interrupted = {"value": False}

    def artifact_for(rows, url):
        content = json.dumps(rows).encode()
        return DownloadedArtifact(
            content=content,
            content_hash=hash_bytes(content),
            content_type="application/json",
            byte_size=len(content),
            extension="json",
            source_url=url,
        )

    def interrupted_fetch(url):
        calls.append(url)
        if len(calls) == 1:
            return artifact_for([records[0]], url)
        if not interrupted["value"]:
            interrupted["value"] = True
            raise RuntimeError("temporary interruption")
        if len(calls) == 3:
            return artifact_for([records[1]], url)
        return artifact_for([], url)

    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-violations").one()
        try:
            load_hpd_violations_snapshot(
                db,
                source,
                requested_mode="full",
                page_size=1,
                fetch_page=interrupted_fetch,
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected simulated interruption")
        checkpoint = db.query(HpdIngestionCheckpoint).one()
        assert checkpoint.is_complete is False
        assert checkpoint.last_page_key == {"violation_id": "1001"}
        assert checkpoint.artifact_manifest_uri

        # The resumed request starts strictly after the persisted key.
        _, created, updated, _ = load_hpd_violations_snapshot(
            db, source, requested_mode="full", page_size=1, fetch_page=interrupted_fetch
        )
        checkpoint = db.query(HpdIngestionCheckpoint).one()
        assert created == 1
        assert updated == 0
        assert checkpoint.is_complete is True
        assert checkpoint.status_date_watermark == date(2026, 1, 2)
        assert db.query(HpdViolation).count() == 2
        assert Path(checkpoint.artifact_manifest_uri).is_file()

        delta = [
            dict(
                records[0],
                currentstatus="Closed",
                currentstatusdate="2025-01-03T00:00:00.000",
            )
        ]
        delta_calls = 0

        def delta_fetch(url):
            nonlocal delta_calls
            delta_calls += 1
            return artifact_for(delta if delta_calls == 1 else [], url)

        _, delta_created, delta_updated, _ = load_hpd_violations_snapshot(
            db, source, requested_mode="delta", page_size=10, fetch_page=delta_fetch
        )
        assert delta_created == 0
        assert delta_updated == 1
        assert (
            db.query(HpdViolation).filter_by(external_id="1001").one().current_status
            == "Closed"
        )


def test_hpd_keyset_urls_and_address_normalization():
    full_url = socrata_page_url(
        "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        "full",
        {"violation_id": "123"},
        None,
        100,
    )
    delta_url = socrata_page_url(
        "https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        "delta",
        None,
        date(2025, 1, 15),
        100,
    )
    assert "violationid+%3E+%27123%27" in full_url
    assert "currentstatusdate+%3E%3D+%272025-01-15%27" in delta_url
    assert normalized_full_address("12-34A", "West 42nd St.") == "1234A WEST42NDST"


def test_postgres_upsert_uses_database_column_names_for_mapped_fields():
    from sqlalchemy.dialects import postgresql

    statement = postgres_upsert_statement(
        [{"external_id": "1001", "violation_class": "C"}]
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT (external_id) DO UPDATE" in sql
    assert "class = excluded.class" in sql
