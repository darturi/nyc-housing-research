from app.db.session import SessionLocal
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.legal_text import parse_legal_document
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.source import Source


def test_good_cause_ingestion_keeps_supported_article_and_notice_sections(tmp_path):
    content = """
    § 209. Prior section not part of Article 6-A.
    Text that must not be ingested.

    § 210. Definitions.
    This article defines covered tenants.

    § 211. Applicability.
    This article applies as provided by law.

    § 216. Severability.
    Invalidity of one provision does not affect another.

    § 217. Later section outside Article 6-A.
    Text that must not be ingested.

    § 231-c. Good cause eviction law notice.
    A landlord shall append the required notice.
    """
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source).filter_by(slug="ny-real-property-law-good-cause").one()
        )
        artifact = DownloadedArtifact(
            content=content.encode(),
            content_hash=hash_bytes(content.encode()),
            content_type="text/plain",
            byte_size=len(content),
            extension="txt",
            source_url=source.source_url,
        )
        version = create_or_get_source_version(db, source, artifact)
        parse_legal_document(db, source, version, content)
        chunks = db.query(Chunk).filter_by(source_id=source.id).all()

    assert [chunk.citation for chunk in chunks] == [
        "Real Property Law § 210",
        "Real Property Law § 211",
        "Real Property Law § 216",
        "Real Property Law § 231-C",
    ]


def test_good_cause_ingestion_accepts_pdf_bullet_section_headings():
    content = """
    * § 210. Short title. This article is the good cause eviction law.
    * § 216. Good cause. A landlord must establish good cause.
    * § 231-c. Notice. A landlord must provide the required notice.
    """
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source).filter_by(slug="ny-real-property-law-good-cause").one()
        )
        artifact = DownloadedArtifact(
            content=content.encode(),
            content_hash=hash_bytes(content.encode()),
            content_type="text/plain",
            byte_size=len(content),
            extension="txt",
            source_url=source.source_url,
        )
        version = create_or_get_source_version(db, source, artifact)
        parse_legal_document(db, source, version, content)
        citations = [chunk.citation for chunk in db.query(Chunk).all()]

    assert citations == [
        "Real Property Law § 210",
        "Real Property Law § 216",
        "Real Property Law § 231-C",
    ]
