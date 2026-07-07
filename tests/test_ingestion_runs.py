from app.db.session import SessionLocal
from app.ingestion.runners import record_ingestion_run
from app.models.ingestion_run import IngestionRun


def test_failed_ingestion_run_records_error_status():
    with SessionLocal() as db:
        try:
            record_ingestion_run(
                db,
                "download",
                lambda: (_ for _ in ()).throw(ValueError("download failed")),
            )
        except ValueError:
            pass

        run = db.query(IngestionRun).one()
        assert run.status == "failed"
        assert "download failed" in run.error_message
