import argparse
import json
import sys

from sqlalchemy import func, or_, select

from app.db.session import SessionLocal
from app.ingestion.artifacts import artifact_exists, read_artifact
from app.ingestion.downloaders import create_or_get_source_version, download_url
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

        def operation():
            artifact = download_url(source.source_url)
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
            raw_text = artifact_bytes_to_text(
                read_artifact(source_version.artifact_uri),
                source_version.content_type,
                source_version.artifact_uri,
            )
            created, updated, skipped = parse_legal_document(
                db,
                source,
                source_version,
                raw_text,
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


def ingest_source_command(source_slug: str) -> None:
    source_version = download_source_command(source_slug)
    parse_source_command(source_slug)
    print(f"Ingested {source_slug} at version {source_version.content_hash}.")


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
            {"chunks": 0, "missing_own_citations": 0, "dirty_chunks": 0},
        )
        if source_status["chunks"] == 0:
            try:
                ingest_source_command(source_slug)
            except Exception as exc:
                failures.append(f"{source_slug}: {exc}")
        elif (
            source_status["missing_own_citations"] > 0
            or source_status["dirty_chunks"] > 0
        ):
            try:
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
        chunks_missing_citation = (
            db.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.citation.is_(None))
            )
            or 0
        )
        chunks_total = count_table(db, Chunk)
    print(f"source_versions: {len(source_versions)}")
    print(f"missing_artifacts: {len(missing_artifacts)}")
    print(f"chunks: {chunks_total}")
    print(f"chunks_missing_citation: {chunks_missing_citation}")
    if missing_artifacts:
        raise RuntimeError(
            "Missing artifacts for source versions: "
            + ", ".join(sorted(missing_artifacts))
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ingestion commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("seed-sources")
    subparsers.add_parser("load-hpd-violations")
    subparsers.add_parser("ingest-mvp")
    subparsers.add_parser("ingest-missing")
    subparsers.add_parser("status")
    subparsers.add_parser("verify-traceability")

    for command in ("download-source", "parse-source", "ingest-source"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("source_slug")

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
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
