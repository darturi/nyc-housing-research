import json
from pathlib import Path

from app.db.session import SessionLocal
from app.ingestion.hpd_violations import upsert_hpd_violations
from app.ingestion.registry import seed_sources
from app.models.hpd_violation import HpdViolation
from app.models.source import Source


def sample_records():
    return json.loads(Path("tests/fixtures/hpd_violations_sample.json").read_text())


def test_hpd_loader_inserts_and_updates_records(tmp_path):
    from app.core.config import get_settings

    get_settings().artifact_storage_path = str(tmp_path)
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

