from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models.source import Source


@dataclass(frozen=True)
class SourceSeed:
    slug: str
    name: str
    source_type: str
    publisher: str
    jurisdiction: str
    source_url: str
    access_type: str
    license_status: str
    terms_url: str | None
    redistribution_allowed: bool | None
    notes: str


MVP_SOURCE_SEEDS = [
    SourceSeed(
        slug="nyc-housing-maintenance-code",
        name="NYC Housing Maintenance Code",
        source_type="law",
        publisher="New York City Council / American Legal Publishing",
        jurisdiction="NYC",
        source_url=(
            "https://codelibrary.amlegal.com/codes/newyorkcity/latest/"
            "NYCadmin/0-0-0-60027"
        ),
        access_type="public_web",
        license_status="public_official_terms_reviewed",
        terms_url="https://www.amlegal.com/terms-of-use/",
        redistribution_allowed=None,
        notes=(
            "Official public NYC code publication; verify reuse terms before "
            "redistribution."
        ),
    ),
    SourceSeed(
        slug="ny-multiple-dwelling-law",
        name="New York Multiple Dwelling Law",
        source_type="law",
        publisher="New York State Senate",
        jurisdiction="NY",
        source_url="https://legislation.nysenate.gov/pdf/laws/MDW?full=true",
        access_type="public_web",
        license_status="public_official",
        terms_url="https://www.nysenate.gov/policies",
        redistribution_allowed=None,
        notes="Official public New York State Senate law PDF endpoint.",
    ),
    SourceSeed(
        slug="ny-rpapl",
        name="New York Real Property Actions and Proceedings Law",
        source_type="law",
        publisher="New York State Senate",
        jurisdiction="NY",
        source_url="https://legislation.nysenate.gov/pdf/laws/RPA?full=true",
        access_type="public_web",
        license_status="public_official",
        terms_url="https://www.nysenate.gov/policies",
        redistribution_allowed=None,
        notes="Official public New York State Senate law PDF endpoint.",
    ),
    SourceSeed(
        slug="hpd-guidance",
        name="HPD Tenant and Owner Guidance",
        source_type="guidance",
        publisher="NYC Department of Housing Preservation and Development",
        jurisdiction="NYC",
        source_url=(
            "https://www.nyc.gov/site/hpd/services-and-information/"
            "services-and-information.page"
        ),
        access_type="public_web",
        license_status="public_official",
        terms_url="https://www.nyc.gov/home/terms-of-use.page",
        redistribution_allowed=None,
        notes="Public HPD guidance landing page.",
    ),
    SourceSeed(
        slug="hpd-violations",
        name="HPD Violations",
        source_type="dataset",
        publisher="NYC Open Data / HPD",
        jurisdiction="NYC",
        source_url="https://data.cityofnewyork.us/resource/wvxf-dwi5.json",
        access_type="public_api",
        license_status="open_data",
        terms_url="https://opendata.cityofnewyork.us/open-data-law/",
        redistribution_allowed=True,
        notes="NYC Open Data HPD violations API endpoint.",
    ),
]


def validate_source_seed(seed: SourceSeed) -> None:
    if not seed.source_url.startswith("https://"):
        raise ValueError(f"{seed.slug}: source_url must be a public HTTPS URL.")
    if not seed.publisher.strip():
        raise ValueError(f"{seed.slug}: publisher is required.")
    if not seed.license_status.strip():
        raise ValueError(f"{seed.slug}: license_status is required.")


def seed_sources(db: DbSession) -> tuple[int, int]:
    created = 0
    updated = 0
    for seed in MVP_SOURCE_SEEDS:
        validate_source_seed(seed)
        source = db.scalar(select(Source).where(Source.slug == seed.slug))
        values = seed.__dict__
        if source is None:
            db.add(Source(**values))
            created += 1
        else:
            for key, value in values.items():
                setattr(source, key, value)
            source.is_active = True
            updated += 1
    db.commit()
    return created, updated


def get_source_by_slug(db: DbSession, slug: str) -> Source:
    source = db.scalar(select(Source).where(Source.slug == slug))
    if source is None:
        raise ValueError(f"Unknown source slug: {slug}")
    return source
