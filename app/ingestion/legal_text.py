import re
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.ingestion.citations import (
    CitationCandidate,
    detect_citation_type,
    extract_citations,
    normalize_citation,
)
from app.ingestion.downloaders import hash_bytes
from app.models.chunk import Chunk
from app.models.citation import Citation
from app.models.document import Document
from app.models.section import Section
from app.models.source import Source
from app.models.source_version import SourceVersion

SKIP_HTML_TAGS = {"script", "style", "noscript", "nav", "footer", "header"}
PAGE_CHROME_LINES = {
    "311",
    "About",
    "Accessibility resources",
    "Careers",
    "Contact",
    "Contact NYC government",
    "Events",
    "Home",
    "Media",
    "Menu",
    "NYC",
    "Print",
    "Privacy policy",
    "Register to vote",
    "Search",
    "Search all NYC.gov websites",
    "Share",
    "Terms of use",
    "Text-Size",
    "Website feedback",
    "Your government",
    "nyc.gov home",
}
PAGE_CHROME_PREFIXES = (
    "!function(",
    "function googleTranslateElementInit",
    "window.",
    "© City of New York",
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in SKIP_HTML_TAGS:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in SKIP_HTML_TAGS and self.skip_depth > 0:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)

    def text(self) -> str:
        return "\n".join(clean_extracted_lines(self.parts))


def clean_extracted_lines(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    for line in lines:
        normalized = re.sub(r"\s+", " ", line).strip()
        if not normalized or is_page_chrome_line(normalized):
            continue
        cleaned.append(normalized)
    return cleaned


def is_page_chrome_line(line: str) -> bool:
    return line in PAGE_CHROME_LINES or any(
        line.startswith(prefix) for prefix in PAGE_CHROME_PREFIXES
    )


@dataclass(frozen=True)
class ParsedSection:
    section_key: str
    citation: str | None
    title: str
    text: str
    order_index: int


SECTION_PATTERN = re.compile(
    r"(?m)^\s*(?P<citation>(?:§\s*)?(?:27-\d{3,5}|MDL\s*§?\s*\d+[a-z]?|"
    r"RPAPL\s*§?\s*\d+[a-z]?|Multiple\s+Dwelling\s+Law\s*§?\s*\d+[a-z]?))"
    r"[\s.:-]+(?P<title>.+)$",
    re.IGNORECASE,
)
STATE_LAW_SECTION_PATTERN = re.compile(
    r"(?m)^\s*§+\s*(?P<section_number>\d+[a-z]?)\s*\.\s*(?P<title>.+)$",
    re.IGNORECASE,
)

STATE_LAW_CITATION_PREFIXES = {
    "ny-multiple-dwelling-law": "Multiple Dwelling Law",
    "ny-rpapl": "RPAPL",
}


def artifact_bytes_to_text(
    content: bytes,
    content_type: str | None,
    artifact_uri: str,
) -> str:
    if _is_pdf_artifact(content_type, artifact_uri):
        return pdf_bytes_to_text(content)
    return content.decode("utf-8", errors="replace")


def _is_pdf_artifact(content_type: str | None, artifact_uri: str) -> bool:
    normalized_content_type = (content_type or "").lower()
    is_pdf_content_type = "application/pdf" in normalized_content_type
    return is_pdf_content_type or artifact_uri.lower().endswith(".pdf")


def pdf_bytes_to_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    page_text = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(part for part in page_text if part.strip())
    if not text.strip():
        raise ValueError("No text could be extracted from PDF artifact.")
    return text


def html_to_text(raw_text: str) -> str:
    if "<" not in raw_text or ">" not in raw_text:
        return raw_text
    parser = TextExtractor()
    parser.feed(raw_text)
    return parser.text()


def normalize_text(raw_text: str) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in raw_text.splitlines()]
    return "\n".join(line for line in lines if line)


def split_sections(
    raw_text: str,
    source_slug: str | None = None,
) -> list[ParsedSection]:
    text = normalize_text(html_to_text(raw_text))
    pattern = section_pattern_for_source(source_slug)
    matches = list(pattern.finditer(text))
    if not matches:
        return [
            ParsedSection(
                section_key="full-text",
                citation=None,
                title="Full Text",
                text=text,
                order_index=0,
            )
        ]

    sections: list[ParsedSection] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section_text = text[start:end].strip()
        citation = citation_from_section_match(match, source_slug)
        key = re.sub(r"[^a-z0-9]+", "-", citation.lower()).strip("-")
        sections.append(
            ParsedSection(
                section_key=key or f"section-{index}",
                citation=citation,
                title=title_from_section_match(match, source_slug),
                text=section_text,
                order_index=index,
            )
        )
    return sections


def section_pattern_for_source(source_slug: str | None) -> re.Pattern:
    if source_slug in STATE_LAW_CITATION_PREFIXES:
        return STATE_LAW_SECTION_PATTERN
    return SECTION_PATTERN


def citation_from_section_match(match: re.Match, source_slug: str | None) -> str:
    if source_slug in STATE_LAW_CITATION_PREFIXES:
        prefix = STATE_LAW_CITATION_PREFIXES[source_slug]
        return f"{prefix} § {match.group('section_number').upper()}"
    return normalize_citation(match.group("citation"))


def title_from_section_match(match: re.Match, source_slug: str | None) -> str:
    title = match.group("title").strip()
    if source_slug in STATE_LAW_CITATION_PREFIXES and "." in title:
        return title.split(".", 1)[0].strip()
    return title


def upsert_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
) -> tuple[Document, bool]:
    document_key = source.slug
    document = db.scalar(
        select(Document).where(
            Document.source_version_id == source_version.id,
            Document.document_key == document_key,
        )
    )
    created = document is None
    if document is None:
        document = Document(
            source_id=source.id,
            source_version_id=source_version.id,
            document_key=document_key,
            title=source.name,
            document_type="guidance" if source.source_type == "guidance" else "statute",
            jurisdiction=source.jurisdiction,
            source_url=source_version.source_url,
        )
        db.add(document)
        db.flush()
    else:
        document.title = source.name
        document.source_url = source_version.source_url
    return document, created


def parse_legal_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
    raw_text: str,
) -> tuple[int, int, int]:
    parsed_sections = split_sections(raw_text, source.slug)
    if source.source_type == "law" and any(
        section.citation is None for section in parsed_sections
    ):
        raise ValueError(
            f"{source.slug} did not split into citation-bearing law sections."
        )
    document, document_created = upsert_document(db, source, source_version)
    created = 1 if document_created else 0
    updated = 0
    skipped = 0
    for parsed_section in parsed_sections:
        section = db.scalar(
            select(Section).where(
                Section.document_id == document.id,
                Section.section_key == parsed_section.section_key,
            )
        )
        section_created = section is None
        if section is None:
            section = Section(
                document_id=document.id,
                section_key=parsed_section.section_key,
                citation=parsed_section.citation,
                title=parsed_section.title,
                hierarchy_path=parsed_section.section_key,
                order_index=parsed_section.order_index,
                text=parsed_section.text,
            )
            db.add(section)
            db.flush()
        else:
            section.citation = parsed_section.citation
            section.title = parsed_section.title
            section.text = parsed_section.text
            section.order_index = parsed_section.order_index
            updated += 1

        text_hash = hash_bytes(normalize_text(parsed_section.text).encode("utf-8"))
        chunk_key = parsed_section.section_key
        chunk = db.scalar(
            select(Chunk).where(
                Chunk.source_version_id == source_version.id,
                Chunk.chunk_key == chunk_key,
            )
        )
        chunk_created = chunk is None
        if chunk is None:
            chunk = Chunk(
                document_id=document.id,
                section_id=section.id,
                source_id=source.id,
                source_version_id=source_version.id,
                chunk_key=chunk_key,
                chunk_type="section",
                citation=parsed_section.citation,
                title=parsed_section.title,
                text=parsed_section.text,
                text_hash=text_hash,
                order_index=parsed_section.order_index,
            )
            db.add(chunk)
            db.flush()
        else:
            chunk.citation = parsed_section.citation
            chunk.title = parsed_section.title
            chunk.text = parsed_section.text
            chunk.text_hash = text_hash
            chunk.order_index = parsed_section.order_index
            updated += 1

        if section_created:
            created += 1
        if chunk_created:
            created += 1

        for candidate in citation_candidates_for_section(parsed_section):
            if not upsert_citation(db, source, document, section, chunk, candidate):
                skipped += 1
                continue
            created += 1
    db.commit()
    return created, updated, skipped


def citation_candidates_for_section(
    parsed_section: ParsedSection,
) -> list[CitationCandidate]:
    candidates: list[CitationCandidate] = []
    seen: set[str] = set()

    def add_candidate(candidate: CitationCandidate) -> None:
        if candidate.normalized_citation in seen:
            return
        seen.add(candidate.normalized_citation)
        candidates.append(candidate)

    if parsed_section.citation is not None:
        normalized = normalize_citation(parsed_section.citation)
        add_candidate(
            CitationCandidate(
                citation_text=parsed_section.citation,
                normalized_citation=normalized,
                citation_type=detect_citation_type(normalized),
            )
        )
    for candidate in extract_citations(parsed_section.text):
        add_candidate(candidate)
    return candidates


def upsert_citation(
    db: DbSession,
    source: Source,
    document: Document,
    section: Section,
    chunk: Chunk,
    candidate: CitationCandidate,
) -> bool:
    existing_citation = db.scalar(
        select(Citation).where(
            Citation.chunk_id == chunk.id,
            Citation.normalized_citation == candidate.normalized_citation,
        )
    )
    if existing_citation is not None:
        return False
    db.add(
        Citation(
            source_id=source.id,
            document_id=document.id,
            section_id=section.id,
            chunk_id=chunk.id,
            citation_text=candidate.citation_text,
            normalized_citation=candidate.normalized_citation,
            citation_type=candidate.citation_type,
        )
    )
    return True
