from dataclasses import dataclass

from app.ingestion.amlegal_xml import AMLEGAL_NYC_ADMIN_XML_ZIP_URL, HMC_XML_MEMBER
from app.ingestion.registry import MVP_SOURCE_SEEDS


@dataclass(frozen=True)
class SourceAcquisition:
    source_slug: str
    mode: str
    automated_enabled: bool
    note: str
    download_url: str | None = None
    artifact_member: str | None = None


@dataclass(frozen=True)
class SourceAvailability:
    source_slug: str
    name: str
    source_url: str
    mode: str
    automated_enabled: bool
    note: str
    download_url: str | None
    artifact_member: str | None


ACQUISITION_BY_SLUG = {
    "nyc-housing-maintenance-code": SourceAcquisition(
        source_slug="nyc-housing-maintenance-code",
        mode="bulk_xml",
        automated_enabled=True,
        note=(
            "Official AmLegal Administrative Code bulk XML ZIP; parse only the "
            "Housing Maintenance Code XML member."
        ),
        download_url=AMLEGAL_NYC_ADMIN_XML_ZIP_URL,
        artifact_member=HMC_XML_MEMBER,
    ),
    "ny-multiple-dwelling-law": SourceAcquisition(
        source_slug="ny-multiple-dwelling-law",
        mode="direct_http",
        automated_enabled=True,
        note="Official NY Senate PDF endpoint.",
    ),
    "ny-rpapl": SourceAcquisition(
        source_slug="ny-rpapl",
        mode="direct_http",
        automated_enabled=True,
        note="Official NY Senate PDF endpoint.",
    ),
    "hpd-guidance": SourceAcquisition(
        source_slug="hpd-guidance",
        mode="hpd_guidance_bundle",
        automated_enabled=True,
        note=(
            "Curated official NYC.gov HPD guidance pages bundled into one "
            "traceable JSON artifact."
        ),
    ),
    "hpd-violations": SourceAcquisition(
        source_slug="hpd-violations",
        mode="public_api",
        automated_enabled=True,
        note="NYC Open Data Socrata API endpoint.",
    ),
}


def acquisition_for_slug(source_slug: str) -> SourceAcquisition:
    return ACQUISITION_BY_SLUG.get(
        source_slug,
        SourceAcquisition(
            source_slug=source_slug,
            mode="direct_http",
            automated_enabled=True,
            note="Default public HTTP source acquisition.",
        ),
    )


def ensure_automated_acquisition_enabled(source_slug: str) -> None:
    acquisition = acquisition_for_slug(source_slug)
    if acquisition.automated_enabled:
        return
    raise ValueError(
        f"{source_slug}: automated ingestion disabled "
        f"({acquisition.mode}). {acquisition.note}"
    )


def source_availability() -> list[SourceAvailability]:
    rows: list[SourceAvailability] = []
    for seed in MVP_SOURCE_SEEDS:
        acquisition = acquisition_for_slug(seed.slug)
        rows.append(
            SourceAvailability(
                source_slug=seed.slug,
                name=seed.name,
                source_url=seed.source_url,
                mode=acquisition.mode,
                automated_enabled=acquisition.automated_enabled,
                note=acquisition.note,
                download_url=acquisition.download_url,
                artifact_member=acquisition.artifact_member,
            )
        )
    return rows
