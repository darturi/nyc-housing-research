from app.db.session import SessionLocal
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.hpd_guidance import (
    HPD_GUIDANCE_PAGE_MAX_RETRIES,
    HPD_GUIDANCE_PAGE_TIMEOUT_SECONDS,
    HpdGuidancePage,
    download_hpd_guidance_bundle,
    guidance_urls,
    hpd_guidance_bundle_bytes,
    parse_hpd_guidance_bundle_document,
    parse_hpd_guidance_page,
)
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.source import Source


def hpd_guidance_html() -> str:
    return """
    <html>
      <head><title>Heat and Hot Water - HPD</title></head>
      <body>
        <nav>Search all NYC.gov websites</nav>
        <aside>
          <p>Affordable Housing</p>
          <p>Building and Land Development Services</p>
        </aside>
        <main>
          <h1>Heat and Hot Water</h1>
          <p>Owners are required to provide heat and hot water to tenants.</p>
          <h2>Report a Heat Complaint</h2>
          <p>Tenants can report a heat or hot water complaint to 311.</p>
          <ul>
            <li>Inspectors may issue violations.</li>
            <li>HPD may contact the property owner.</li>
          </ul>
        </main>
        <footer>© City of New York. 2026 All Rights Reserved.</footer>
      </body>
    </html>
    """


def second_hpd_guidance_html() -> str:
    return """
    <html>
      <head><title>Lead-Based Paint - HPD</title></head>
      <body>
        <main>
          <h1>Lead-Based Paint</h1>
          <p>Owners must follow lead-based paint safety requirements.</p>
        </main>
      </body>
    </html>
    """


def test_parse_hpd_guidance_page_chunks_by_heading_and_removes_chrome():
    sections = parse_hpd_guidance_page(
        "https://www.nyc.gov/site/hpd/services-and-information/heat-and-hot-water.page",
        "Heat and Hot Water",
        hpd_guidance_html(),
    )

    assert len(sections) == 2
    assert sections[0].title == "Heat and Hot Water"
    assert "provide heat and hot water" in sections[0].text
    assert sections[1].title == "Report a Heat Complaint"
    assert "Inspectors may issue violations" in sections[1].text
    combined = "\n".join(section.text for section in sections)
    assert "Search all NYC.gov websites" not in combined
    assert "Affordable Housing" not in combined
    assert "© City of New York" not in combined


def test_download_hpd_guidance_bundle_uses_bounded_page_downloads(monkeypatch):
    calls = []

    def fake_download_url(
        url: str,
        *,
        timeout_seconds: int | None = None,
        max_retries: int | None = None,
    ):
        content = hpd_guidance_html().encode()
        calls.append((url, timeout_seconds, max_retries))
        return DownloadedArtifact(
            content=content,
            content_hash=hash_bytes(content),
            content_type="text/html",
            byte_size=len(content),
            extension="html",
            source_url=url,
        )

    monkeypatch.setattr("app.ingestion.hpd_guidance.download_url", fake_download_url)
    messages: list[str] = []
    source_url = (
        "https://www.nyc.gov/site/hpd/services-and-information/"
        "services-and-information.page"
    )

    artifact = download_hpd_guidance_bundle(source_url, progress=messages.append)

    expected_urls = guidance_urls(source_url)
    assert artifact.content_type == "application/json"
    assert [call[0] for call in calls] == expected_urls
    assert all(call[1] == HPD_GUIDANCE_PAGE_TIMEOUT_SECONDS for call in calls)
    assert all(call[2] == HPD_GUIDANCE_PAGE_MAX_RETRIES for call in calls)
    assert messages[0].startswith("Downloading HPD guidance page 1/")


def test_parse_hpd_guidance_bundle_persists_page_documents_and_uncited_chunks(tmp_path):
    from app.core.config import get_settings

    settings = get_settings()
    settings.artifact_storage_backend = "local"
    settings.artifact_storage_path = str(tmp_path)
    pages = [
        HpdGuidancePage(
            url=(
                "https://www.nyc.gov/site/hpd/services-and-information/"
                "heat-and-hot-water.page"
            ),
            title="Heat and Hot Water",
            html=hpd_guidance_html(),
        ),
        HpdGuidancePage(
            url=(
                "https://www.nyc.gov/site/hpd/services-and-information/"
                "lead-based-paint.page"
            ),
            title="Lead-Based Paint",
            html=second_hpd_guidance_html(),
        ),
    ]
    content = hpd_guidance_bundle_bytes(pages)
    artifact = DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type="application/json",
        byte_size=len(content),
        extension="json",
        source_url="https://www.nyc.gov/site/hpd/services-and-information/services-and-information.page",
    )
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter_by(slug="hpd-guidance").one()
        source_version = create_or_get_source_version(db, source, artifact)

        created, updated, skipped = parse_hpd_guidance_bundle_document(
            db,
            source,
            source_version,
            content,
        )

        documents = db.query(Document).order_by(Document.title).all()
        chunks = db.query(Chunk).order_by(Chunk.order_index).all()

    assert created > 0
    assert updated == 0
    assert skipped == 0
    assert len(documents) == 2
    assert all(
        document.source_url.startswith("https://www.nyc.gov/")
        for document in documents
    )
    assert len(chunks) == 3
    assert all(chunk.citation is None for chunk in chunks)
    assert all(chunk.chunk_type == "guidance_section" for chunk in chunks)
