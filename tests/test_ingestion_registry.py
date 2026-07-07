from app.db.session import SessionLocal
from app.ingestion.registry import SourceSeed, seed_sources, validate_source_seed
from app.models.source import Source


def test_seed_sources_is_idempotent():
    with SessionLocal() as db:
        created, updated = seed_sources(db)
        assert created == 5
        assert updated == 0

        created, updated = seed_sources(db)
        assert created == 0
        assert updated == 5
        assert db.query(Source).count() == 5


def test_validate_source_seed_requires_public_url_and_license_status():
    seed = SourceSeed(
        slug="bad",
        name="Bad",
        source_type="law",
        publisher="Publisher",
        jurisdiction="NYC",
        source_url="http://example.com",
        access_type="public_web",
        license_status="",
        terms_url=None,
        redistribution_allowed=None,
        notes="",
    )

    try:
        validate_source_seed(seed)
    except ValueError as exc:
        assert "source_url" in str(exc)
    else:
        raise AssertionError("Expected invalid seed to raise ValueError")

