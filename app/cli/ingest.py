import argparse
import json
import sys
from dataclasses import replace
from mimetypes import guess_type
from pathlib import Path

from sqlalchemy import func, or_, select

from app.db.session import SessionLocal
from app.ingestion.acquisition import (
    acquisition_for_slug,
    ensure_automated_acquisition_enabled,
    source_availability,
)
from app.ingestion.amlegal_xml import parse_hmc_bulk_xml_document
from app.ingestion.artifacts import artifact_exists, read_artifact
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    download_url,
    extension_from_content_type,
    hash_bytes,
)
from app.ingestion.hpd_guidance import (
    download_hpd_guidance_bundle,
    parse_hpd_guidance_bundle_document,
    parse_hpd_guidance_html_document,
)
from app.ingestion.hpd_violations import upsert_hpd_violations
from app.ingestion.legal_text import artifact_bytes_to_text, parse_legal_document
from app.ingestion.registry import get_source_by_slug, seed_sources
from app.ingestion.runners import record_ingestion_run
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.hpd_violation import HpdViolation
from app.models.ingestion_run import IngestionRun
from app.models.source import Source
from app.models.source_version import SourceVersion

LEGAL_SOURCE_SLUGS = [
    "nyc-housing-maintenance-code",
    "ny-multiple-dwelling-law",
    "ny-rpapl",
    "hpd-guidance",
]
DIRTY_CHUNK_MARKERS = (
    "BOOMR",
    "Search all NYC.gov websites",
    "function googleTranslateElementInit",
)


def seed_sources_command() -> None:
    with SessionLocal() as db:
        def operation():
            created, updated = seed_sources(db)
            return None, created, updated, 0

        record_ingestion_run(db, "registry_seed", operation)
        print("Seeded source registry.")


def download_source_command(source_slug: str) -> SourceVersion:
    with SessionLocal() as db:
        source = get_source_by_slug(db, source_slug)
        ensure_automated_acquisition_enabled(source.slug)

        def operation():
            artifact = download_source_artifact(source)
            source_version = create_or_get_source_version(db, source, artifact)
            return source_version, 1, 0, 0

        source_version = record_ingestion_run(
            db,
            "download",
            operation,
            source_id=source.id,
        )
        print(f"Downloaded {source.slug}: {source_version.content_hash}")
        return source_version


def download_source_artifact(source: Source) -> DownloadedArtifact:
    acquisition = acquisition_for_slug(source.slug)
    if acquisition.mode == "hpd_guidance_bundle":
        return download_hpd_guidance_bundle(
            source.source_url,
            progress=print_download_progress,
        )
    artifact = download_url(acquisition.download_url or source.source_url)
    if acquisition.mode == "bulk_xml":
        return replace(artifact, source_url=source.source_url)
    return artifact


def print_download_progress(message: str) -> None:
    print(message, flush=True)


def current_source_version(db, source: Source) -> SourceVersion:
    source_version = db.scalar(
        select(SourceVersion)
        .where(SourceVersion.source_id == source.id)
        .order_by(SourceVersion.retrieved_at.desc())
    )
    if source_version is None:
        raise ValueError(f"No source version found for {source.slug}; download first.")
    return source_version


def parse_source_command(source_slug: str) -> None:
    with SessionLocal() as db:
        source = get_source_by_slug(db, source_slug)
        source_version = current_source_version(db, source)

        def operation():
            created, updated, skipped = parse_source_artifact(
                db,
                source,
                source_version,
                read_artifact(source_version.artifact_uri),
            )
            return None, created, updated, skipped

        record_ingestion_run(
            db,
            "parse",
            operation,
            source_id=source.id,
            source_version_id=source_version.id,
        )
        print(f"Parsed {source.slug}.")


def parse_source_artifact(
    db,
    source: Source,
    source_version: SourceVersion,
    content: bytes,
) -> tuple[int, int, int]:
    if source.slug == "nyc-housing-maintenance-code" and is_zip_artifact(
        source_version,
    ):
        return parse_hmc_bulk_xml_document(db, source, source_version, content)
    if source.slug == "hpd-guidance" and is_json_artifact(source_version):
        return parse_hpd_guidance_bundle_document(db, source, source_version, content)
    if source.slug == "hpd-guidance":
        return parse_hpd_guidance_html_document(db, source, source_version, content)
    raw_text = artifact_bytes_to_text(
        content,
        source_version.content_type,
        source_version.artifact_uri,
    )
    return parse_legal_document(db, source, source_version, raw_text)


def is_zip_artifact(source_version: SourceVersion) -> bool:
    content_type = (source_version.content_type or "").lower()
    return "zip" in content_type or source_version.artifact_uri.lower().endswith(".zip")


def is_json_artifact(source_version: SourceVersion) -> bool:
    content_type = (source_version.content_type or "").lower()
    return "json" in content_type or source_version.artifact_uri.lower().endswith(
        ".json"
    )


def ingest_source_command(source_slug: str) -> None:
    source_version = download_source_command(source_slug)
    parse_source_command(source_slug)
    print(f"Ingested {source_slug} at version {source_version.content_hash}.")


def local_file_artifact(
    file_path: str,
    source_url: str,
    content_type: str | None,
) -> DownloadedArtifact:
    if not source_url.startswith("https://"):
        raise ValueError("source-url must be a public HTTPS URL.")
    path = Path(file_path)
    if not path.is_file():
        raise ValueError(f"Artifact file not found: {file_path}")
    resolved_content_type = content_type or guess_type(path.name)[0]
    content = path.read_bytes()
    extension = extension_from_content_type(resolved_content_type, source_url)
    if extension == "bin" and path.suffix:
        extension = path.suffix.lstrip(".").lower()
    return DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type=resolved_content_type,
        byte_size=len(content),
        extension=extension,
        source_url=source_url,
    )


def ingest_artifact_command(
    source_slug: str,
    file_path: str,
    source_url: str,
    content_type: str | None,
) -> None:
    with SessionLocal() as db:
        source = get_source_by_slug(db, source_slug)

        def operation():
            artifact = local_file_artifact(file_path, source_url, content_type)
            existing_source_version = db.scalar(
                select(SourceVersion).where(
                    SourceVersion.source_id == source.id,
                    SourceVersion.content_hash == artifact.content_hash,
                )
            )
            source_version = create_or_get_source_version(db, source, artifact)
            created = 0 if existing_source_version is not None else 1
            updated = 0
            skipped = 0
            if source_slug in LEGAL_SOURCE_SLUGS:
                parsed_created, parsed_updated, parsed_skipped = parse_source_artifact(
                    db,
                    source,
                    source_version,
                    artifact.content,
                )
                created += parsed_created
                updated += parsed_updated
                skipped += parsed_skipped
            return source_version, created, updated, skipped

        source_version = record_ingestion_run(
            db,
            "ingest_artifact",
            operation,
            source_id=source.id,
        )
        print(
            f"Ingested artifact for {source.slug}: "
            f"{source_version.content_hash}"
        )


def load_hpd_violations_command() -> None:
    with SessionLocal() as db:
        source = get_source_by_slug(db, "hpd-violations")

        def operation():
            artifact = download_url(f"{source.source_url}?$limit=5000")
            records = json.loads(artifact.content.decode("utf-8"))
            source_version = create_or_get_source_version(db, source, artifact)
            _, created, updated, skipped = upsert_hpd_violations(
                db,
                source,
                records,
                source_version,
            )
            return None, created, updated, skipped

        record_ingestion_run(db, "load_dataset", operation, source_id=source.id)
        print("Loaded HPD violations.")


def ingest_mvp_command() -> None:
    seed_sources_command()
    for source_slug in LEGAL_SOURCE_SLUGS:
        ingest_source_command(source_slug)
    load_hpd_violations_command()
    print("Completed MVP ingestion.")


def ingest_missing_command() -> None:
    seed_sources_command()
    with SessionLocal() as db:
        legal_status = {
            source.slug: {
                "chunks": count_table(db, Chunk, source_id=source.id),
                "missing_own_citations": count_chunks_missing_own_citation(
                    db,
                    source.id,
                ),
                "dirty_chunks": count_dirty_chunks(db, source.id),
                "broad_guidance_chunks": count_broad_guidance_chunks(db, source.id),
            }
            for source in db.scalars(
                select(Source).where(Source.slug.in_(LEGAL_SOURCE_SLUGS))
            )
        }
        hpd_count = count_table(
            db,
            HpdViolation,
        )

    failures: list[str] = []
    for source_slug in LEGAL_SOURCE_SLUGS:
        source_status = legal_status.get(
            source_slug,
            {
                "chunks": 0,
                "missing_own_citations": 0,
                "dirty_chunks": 0,
                "broad_guidance_chunks": 0,
            },
        )
        if source_status["chunks"] == 0:
            acquisition = acquisition_for_slug(source_slug)
            if not acquisition.automated_enabled:
                failures.append(
                    f"{source_slug}: automated ingestion disabled "
                    f"({acquisition.mode}). {acquisition.note}"
                )
                continue
            try:
                ingest_source_command(source_slug)
            except Exception as exc:
                failures.append(f"{source_slug}: {exc}")
        elif (
            source_status["missing_own_citations"] > 0
            or source_status["dirty_chunks"] > 0
            or (
                source_slug == "hpd-guidance"
                and source_status["broad_guidance_chunks"] > 0
            )
        ):
            try:
                if source_slug == "hpd-guidance":
                    ingest_source_command(source_slug)
                else:
                    parse_source_command(source_slug)
            except Exception as exc:
                failures.append(f"{source_slug}: {exc}")

    if hpd_count == 0:
        try:
            load_hpd_violations_command()
        except Exception as exc:
            failures.append(f"hpd-violations: {exc}")
    print("Completed missing-source ingestion.")
    if failures:
        print("Warning: some sources failed to ingest.", file=sys.stderr)
        for failure in failures:
            print(f"Warning: {failure}", file=sys.stderr)


def count_table(db, model, **filters) -> int:
    query = select(func.count()).select_from(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return db.scalar(query) or 0


def count_chunks_missing_own_citation(db, source_id: str) -> int:
    citation_exists = (
        select(Citation.id)
        .where(
            Citation.chunk_id == Chunk.id,
            Citation.normalized_citation == Chunk.citation,
        )
        .exists()
    )
    return (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.source_id == source_id,
                Chunk.citation.is_not(None),
                ~citation_exists,
            )
        )
        or 0
    )


def count_dirty_chunks(db, source_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.source_id == source_id,
                or_(*(Chunk.text.contains(marker) for marker in DIRTY_CHUNK_MARKERS)),
            )
        )
        or 0
    )


def count_broad_guidance_chunks(db, source_id: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Chunk)
            .join(SourceVersion, SourceVersion.id == Chunk.source_version_id)
            .where(
                Chunk.source_id == source_id,
                SourceVersion.is_current.is_(True),
                Chunk.chunk_type == "section",
                Chunk.citation.is_(None),
                Chunk.title == "Full Text",
            )
        )
        or 0
    )


def status_command() -> None:
    with SessionLocal() as db:
        counts = {
            "sources": count_table(db, Source),
            "source_versions": count_table(db, SourceVersion),
            "documents": count_table(db, Document),
            "chunks": count_table(db, Chunk),
            "hpd_violations": count_table(db, HpdViolation),
            "ingestion_runs": count_table(db, IngestionRun),
        }
    for name, count in counts.items():
        print(f"{name}: {count}")


def verify_traceability_command() -> None:
    with SessionLocal() as db:
        source_versions = db.scalars(select(SourceVersion)).all()
        missing_artifacts = [
            source_version.id
            for source_version in source_versions
            if not artifact_exists(source_version.artifact_uri)
        ]
        law_chunks_missing_citation = (
            db.scalar(
                select(func.count())
                .select_from(Chunk)
                .join(Source, Source.id == Chunk.source_id)
                .where(Source.source_type == "law")
                .where(Chunk.citation.is_(None))
            )
            or 0
        )
        guidance_chunks_without_citation = (
            db.scalar(
                select(func.count())
                .select_from(Chunk)
                .join(Source, Source.id == Chunk.source_id)
                .where(Source.source_type == "guidance")
                .where(Chunk.citation.is_(None))
            )
            or 0
        )
        chunks_total = count_table(db, Chunk)
    print(f"source_versions: {len(source_versions)}")
    print(f"missing_artifacts: {len(missing_artifacts)}")
    print(f"chunks: {chunks_total}")
    print(f"law_chunks_missing_citation: {law_chunks_missing_citation}")
    print(f"guidance_chunks_without_citation: {guidance_chunks_without_citation}")
    if missing_artifacts:
        raise RuntimeError(
            "Missing artifacts for source versions: "
            + ", ".join(sorted(missing_artifacts))
        )


def source_availability_command() -> None:
    for row in source_availability():
        automated = "true" if row.automated_enabled else "false"
        print(
            f"{row.source_slug}: mode={row.mode} "
            f"automated={automated}"
        )
        print(f"  name: {row.name}")
        print(f"  url: {row.source_url}")
        if row.download_url:
            print(f"  download_url: {row.download_url}")
        if row.artifact_member:
            print(f"  artifact_member: {row.artifact_member}")
        print(f"  note: {row.note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ingestion commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("seed-sources")
    subparsers.add_parser("load-hpd-violations")
    subparsers.add_parser("ingest-mvp")
    subparsers.add_parser("ingest-missing")
    subparsers.add_parser("status")
    subparsers.add_parser("verify-traceability")
    subparsers.add_parser("source-availability")

    for command in ("download-source", "parse-source", "ingest-source"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("source_slug")

    ingest_artifact = subparsers.add_parser("ingest-artifact")
    ingest_artifact.add_argument("source_slug")
    ingest_artifact.add_argument("--file", required=True)
    ingest_artifact.add_argument("--source-url", required=True)
    ingest_artifact.add_argument("--content-type")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "seed-sources":
            seed_sources_command()
        elif args.command == "download-source":
            download_source_command(args.source_slug)
        elif args.command == "parse-source":
            parse_source_command(args.source_slug)
        elif args.command == "ingest-source":
            ingest_source_command(args.source_slug)
        elif args.command == "ingest-artifact":
            ingest_artifact_command(
                args.source_slug,
                args.file,
                args.source_url,
                args.content_type,
            )
        elif args.command == "load-hpd-violations":
            load_hpd_violations_command()
        elif args.command == "ingest-mvp":
            ingest_mvp_command()
        elif args.command == "ingest-missing":
            ingest_missing_command()
        elif args.command == "status":
            status_command()
        elif args.command == "verify-traceability":
            verify_traceability_command()
        elif args.command == "source-availability":
            source_availability_command()
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
