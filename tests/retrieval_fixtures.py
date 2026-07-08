from app.auth.password import hash_password
from app.cli.embeddings import generate_embeddings
from app.db.session import SessionLocal
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.hpd_guidance import (
    HpdGuidancePage,
    hpd_guidance_bundle_bytes,
    parse_hpd_guidance_bundle_document,
)
from app.ingestion.legal_text import parse_legal_document
from app.ingestion.registry import seed_sources
from app.models.source import Source
from app.models.user import User

TEST_PASSWORD = "correct horse battery staple"


def create_test_user(email: str = "admin@example.com", is_admin: bool = True) -> User:
    with SessionLocal() as db:
        user = User(
            email=email,
            password_hash=hash_password(TEST_PASSWORD),
            is_admin=is_admin,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def create_retrieval_corpus() -> None:
    content = """
    § 27-2005 Duties of owner.
    The owner of a multiple dwelling shall keep the premises in good repair.

    § 27-2029 Heat required.
    Owners must provide heat during the heat season.
    """
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source)
            .filter_by(slug="nyc-housing-maintenance-code")
            .one()
        )
        artifact = DownloadedArtifact(
            content=content.encode("utf-8"),
            content_hash=hash_bytes(content.encode("utf-8")),
            content_type="text/plain",
            byte_size=len(content.encode("utf-8")),
            extension="txt",
            source_url=source.source_url,
        )
        version = create_or_get_source_version(db, source, artifact)
        parse_legal_document(db, source, version, content)
    generate_embeddings()


def create_hmc_quality_corpus() -> None:
    content = """
    § 27-2005 Duties of owner.
    The owner of a multiple dwelling shall keep the premises in good repair.

    § 27-2029 Minimum temperature to be maintained.
    From October first through May thirty-first, owners must maintain minimum
    temperature in dwellings when heat is required.

    § 27-2047 Mail service.
    The owner of a multiple dwelling shall arrange mail service for prompt
    distribution to occupants.
    """
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source)
            .filter_by(slug="nyc-housing-maintenance-code")
            .one()
        )
        artifact = DownloadedArtifact(
            content=content.encode("utf-8"),
            content_hash=hash_bytes(content.encode("utf-8")),
            content_type="text/plain",
            byte_size=len(content.encode("utf-8")),
            extension="txt",
            source_url=source.source_url,
        )
        version = create_or_get_source_version(db, source, artifact)
        parse_legal_document(db, source, version, content)
    generate_embeddings()


def create_hmc_corpus_with_superseded_version() -> tuple[str, str]:
    old_content = """
    § 27-2005 Duties of owner.
    This obsolete owner standard should not appear in current retrieval.
    """
    current_content = """
    § 27-2005 Duties of owner.
    The current owner standard requires keeping premises in good repair.
    """
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source)
            .filter_by(slug="nyc-housing-maintenance-code")
            .one()
        )
        old_artifact = DownloadedArtifact(
            content=old_content.encode("utf-8"),
            content_hash=hash_bytes(old_content.encode("utf-8")),
            content_type="text/plain",
            byte_size=len(old_content.encode("utf-8")),
            extension="txt",
            source_url=f"{source.source_url}?version=old",
        )
        old_version = create_or_get_source_version(db, source, old_artifact)
        parse_legal_document(db, source, old_version, old_content)

        current_artifact = DownloadedArtifact(
            content=current_content.encode("utf-8"),
            content_hash=hash_bytes(current_content.encode("utf-8")),
            content_type="text/plain",
            byte_size=len(current_content.encode("utf-8")),
            extension="txt",
            source_url=f"{source.source_url}?version=current",
        )
        current_version = create_or_get_source_version(db, source, current_artifact)
        parse_legal_document(db, source, current_version, current_content)
        old_version_id = old_version.id
        current_version_id = current_version.id
    generate_embeddings()
    return old_version_id, current_version_id


def create_hpd_guidance_quality_corpus() -> None:
    pages = [
        HpdGuidancePage(
            url=(
                "https://www.nyc.gov/site/hpd/services-and-information/"
                "report-a-housing-complaint.page"
            ),
            title="Report a Housing Complaint",
            html="""
            <html><body><main>
              <h1>Report a Housing Complaint</h1>
              <p>Tenants can report housing complaints to 311.</p>
              <p>HPD may inspect and issue violations.</p>
            </main></body></html>
            """,
        ),
        HpdGuidancePage(
            url=(
                "https://www.nyc.gov/site/hpd/services-and-information/"
                "enforcement.page"
            ),
            title="Enforcement",
            html="""
            <html><body><main>
              <h1>Enforcement</h1>
              <p>HPD enforcement includes inspections, violations, and owner
              correction requirements.</p>
            </main></body></html>
            """,
        ),
    ]
    content = hpd_guidance_bundle_bytes(pages)
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-guidance").one()
        artifact = DownloadedArtifact(
            content=content,
            content_hash=hash_bytes(content),
            content_type="application/json",
            byte_size=len(content),
            extension="json",
            source_url=source.source_url,
        )
        version = create_or_get_source_version(db, source, artifact)
        parse_hpd_guidance_bundle_document(db, source, version, content)
    generate_embeddings()


def create_rpapl_corpus_with_guidance_noise() -> None:
    rpapl_content = """
    ARTICLE 7
      § 711. Grounds where landlord-tenant relationship exists.
      A tenant shall include an occupant of one or more rooms in a rooming house.
      1. The tenant continues in possession after the expiration of the term.
      2. The tenant has defaulted in the payment of rent after a written demand.

      § 1052. Fees of surveyor or commissioner in action for dower.
      A surveyor is entitled to fees for necessary surveying work.
    """
    guidance_html = """
    <html>
      <head><script>!function(a){window.BOOMR = true;}</script></head>
      <body>
        <nav>Search all NYC.gov websites</nav>
        <main>
          <h1>Services and Information - HPD</h1>
          <p>Tenants can find housing quality and safety information.</p>
        </main>
        <footer>© City of New York. 2025 All Rights Reserved.</footer>
      </body>
    </html>
    """
    with SessionLocal() as db:
        seed_sources(db)
        rpapl_source = db.query(Source).filter_by(slug="ny-rpapl").one()
        rpapl_artifact = DownloadedArtifact(
            content=rpapl_content.encode("utf-8"),
            content_hash=hash_bytes(rpapl_content.encode("utf-8")),
            content_type="text/plain",
            byte_size=len(rpapl_content.encode("utf-8")),
            extension="txt",
            source_url=rpapl_source.source_url,
        )
        rpapl_version = create_or_get_source_version(db, rpapl_source, rpapl_artifact)
        parse_legal_document(db, rpapl_source, rpapl_version, rpapl_content)

        guidance_source = db.query(Source).filter_by(slug="hpd-guidance").one()
        guidance_artifact = DownloadedArtifact(
            content=guidance_html.encode("utf-8"),
            content_hash=hash_bytes(guidance_html.encode("utf-8")),
            content_type="text/html",
            byte_size=len(guidance_html.encode("utf-8")),
            extension="html",
            source_url=guidance_source.source_url,
        )
        guidance_version = create_or_get_source_version(
            db,
            guidance_source,
            guidance_artifact,
        )
        parse_legal_document(db, guidance_source, guidance_version, guidance_html)
    generate_embeddings()
