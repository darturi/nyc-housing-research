from app.auth.password import hash_password
from app.cli.embeddings import generate_embeddings
from app.db.session import SessionLocal
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
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
