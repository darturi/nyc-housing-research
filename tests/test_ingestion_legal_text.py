from pathlib import Path

from app.db.session import SessionLocal
from app.ingestion.downloaders import (
    DownloadedArtifact,
    create_or_get_source_version,
    hash_bytes,
)
from app.ingestion.legal_text import (
    artifact_bytes_to_text,
    html_to_text,
    parse_legal_document,
    split_sections,
)
from app.ingestion.registry import seed_sources
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.section import Section
from app.models.source import Source


def sample_html() -> str:
    return Path("tests/fixtures/hmc_sample.html").read_text()


def test_split_sections_detects_hmc_sections():
    sections = split_sections(sample_html())

    assert len(sections) == 2
    assert sections[0].citation == "NYC Admin Code § 27-2005"
    assert "Duties of owner" in sections[0].title


def test_split_sections_labels_state_law_pdf_sections():
    raw_text = """
    ARTICLE I
      § 1. Short title. This chapter shall be known as the multiple dwelling law.
      § 2. Legislative finding. It is hereby declared that standards are needed.
    """

    sections = split_sections(raw_text, "ny-multiple-dwelling-law")

    assert len(sections) == 2
    assert sections[0].citation == "Multiple Dwelling Law § 1"
    assert sections[0].title == "Short title"
    assert sections[1].citation == "Multiple Dwelling Law § 2"


def test_artifact_bytes_to_text_extracts_pdf_text(monkeypatch):
    class FakePage:
        def __init__(self, text):
            self.text = text

        def extract_text(self):
            return self.text

    class FakeReader:
        def __init__(self, stream):
            self.stream = stream
            self.pages = [FakePage("Page one"), FakePage("Page two")]

    monkeypatch.setattr("app.ingestion.legal_text.PdfReader", FakeReader)

    text = artifact_bytes_to_text(b"%PDF", "application/pdf", "source.pdf")

    assert text == "Page one\nPage two"


def test_html_to_text_removes_scripts_and_page_chrome():
    raw_html = """
    <html>
      <head><script>!function(a){window.BOOMR = true;}</script></head>
      <body>
        <nav>Search all NYC.gov websites</nav>
        <main>
          <h1>Tenant Rights</h1>
          <p>Owners must keep apartments safe.</p>
        </main>
        <footer>© City of New York. 2025 All Rights Reserved.</footer>
      </body>
    </html>
    """

    text = html_to_text(raw_html)

    assert "!function" not in text
    assert "Search all NYC.gov websites" not in text
    assert "© City of New York" not in text
    assert "Tenant Rights" in text
    assert "Owners must keep apartments safe." in text


def test_parse_state_law_indexes_section_own_citation(tmp_path):
    from app.core.config import get_settings

    get_settings().artifact_storage_path = str(tmp_path)
    content = """
    ARTICLE 7
      § 711. Grounds where landlord-tenant relationship exists.
      A tenant shall include an occupant of one or more rooms.
    """
    artifact = DownloadedArtifact(
        content=content.encode("utf-8"),
        content_hash=hash_bytes(content.encode("utf-8")),
        content_type="text/plain",
        byte_size=len(content.encode("utf-8")),
        extension="txt",
        source_url="https://example.com/rpapl.txt",
    )
    with SessionLocal() as db:
        seed_sources(db)
        source = db.query(Source).filter(Source.slug == "ny-rpapl").one()
        source_version = create_or_get_source_version(db, source, artifact)

        parse_legal_document(db, source, source_version, content)

        citation = (
            db.query(Citation)
            .filter(Citation.normalized_citation == "RPAPL § 711")
            .one()
        )
        chunk = db.query(Chunk).filter(Chunk.citation == "RPAPL § 711").one()
        assert citation.chunk_id == chunk.id


def test_parse_legal_document_is_idempotent(tmp_path):
    from app.core.config import get_settings

    get_settings().artifact_storage_path = str(tmp_path)
    content = sample_html().encode("utf-8")
    artifact = DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type="text/html",
        byte_size=len(content),
        extension="html",
        source_url="https://example.com/hmc.html",
    )
    with SessionLocal() as db:
        seed_sources(db)
        source = (
            db.query(Source)
            .filter(Source.slug == "nyc-housing-maintenance-code")
            .one()
        )
        source_version = create_or_get_source_version(db, source, artifact)

        created, updated, skipped = parse_legal_document(
            db,
            source,
            source_version,
            sample_html(),
        )
        second_created, second_updated, second_skipped = parse_legal_document(
            db,
            source,
            source_version,
            sample_html(),
        )

        assert created > 0
        assert updated == 0
        assert skipped == 0
        assert second_created == 0
        assert second_updated > 0
        assert second_skipped > 0
        assert db.query(Document).count() == 1
        assert db.query(Section).count() == 2
        assert db.query(Chunk).count() == 2
        assert db.query(Citation).count() == 2
