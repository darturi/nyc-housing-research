import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.core.config import get_settings
from app.ingestion.downloaders import DownloadedArtifact, download_url, hash_bytes
from app.ingestion.legal_text import (
    ParsedSection,
    is_page_chrome_line,
    normalize_text,
)
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.section import Section
from app.models.source import Source
from app.models.source_version import SourceVersion

HPD_GUIDANCE_URLS = (
    "https://www.nyc.gov/site/hpd/services-and-information/services-and-information.page",
    "https://www.nyc.gov/site/hpd/services-and-information/housing-quality-and-safety.page",
    "https://www.nyc.gov/site/hpd/services-and-information/report-a-housing-complaint.page",
    "https://www.nyc.gov/site/hpd/services-and-information/tenants-rights.page",
    "https://www.nyc.gov/site/hpd/services-and-information/enforcement.page",
)
HPD_GUIDANCE_ALLOWED_PREFIX = "https://www.nyc.gov/site/hpd/services-and-information/"
HPD_BUNDLE_FORMAT = "hpd_guidance_bundle_v1"
HPD_GUIDANCE_PAGE_TIMEOUT_SECONDS = 10
HPD_GUIDANCE_PAGE_MAX_RETRIES = 1
SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg"}
CONTENT_BLOCK_TAGS = {"p", "li", "dt", "dd"}
HEADING_TAGS = {"h1", "h2", "h3"}
SKIP_ATTR_PATTERN = re.compile(
    r"nav|breadcrumb|footer|header|search|share|social|translate|language|"
    r"google|print|sidebar|side-nav|nycgov",
    re.IGNORECASE,
)
HPD_CHROME_LINES = {
    "Affordable Housing",
    "Building and Land Development Services",
    "Code Enforcement",
    "Compliance",
    "Design Guidelines",
    "Do Business with HPD",
    "Expediting New Affordable Housing",
    "Guide for NYC Homeowners",
    "Home Repair and Preservation Financing",
    "Housing Quality / Safety",
    "Neighborhood Planning",
    "New Construction Financing",
    "Property Management",
    "Rental and Down Payment Assistance",
    "Section 8 / Rental Subsidy Programs",
    "Services and Information",
}


@dataclass(frozen=True)
class HpdGuidancePage:
    url: str
    title: str
    html: str


class HpdGuidanceParser(HTMLParser):
    def __init__(self, fallback_title: str) -> None:
        super().__init__()
        self.fallback_title = fallback_title
        self.title_parts: list[str] = []
        self.current_section_title: str | None = None
        self.current_section_lines: list[str] = []
        self.sections: list[ParsedSection] = []
        self.skip_depth = 0
        self.current_block_tag: str | None = None
        self.current_block_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag in SKIP_TAGS or has_skip_attr(attrs):
            self._flush_block()
            self.skip_depth = 1
            return
        if tag in HEADING_TAGS or tag in CONTENT_BLOCK_TAGS or tag == "title":
            self._flush_block()
            self.current_block_tag = tag
            self.current_block_parts = []
        elif tag == "br" and self.current_block_tag:
            self.current_block_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if self.current_block_tag == tag:
            self._flush_block()

    def handle_data(self, data: str) -> None:
        if self.skip_depth or not self.current_block_tag:
            return
        if data.strip():
            self.current_block_parts.append(data)

    def close(self) -> None:
        self._flush_block()
        self._flush_section()
        super().close()

    def _flush_block(self) -> None:
        if self.current_block_tag is None:
            return
        text = clean_guidance_line(" ".join(self.current_block_parts))
        tag = self.current_block_tag
        self.current_block_tag = None
        self.current_block_parts = []
        if not text:
            return
        if tag == "title":
            self.title_parts.append(text)
            return
        if tag in HEADING_TAGS:
            self._start_section(text)
            return
        if self.current_section_title is None:
            self._start_section(self.title())
        self.current_section_lines.append(text)

    def _start_section(self, title: str) -> None:
        self._flush_section()
        self.current_section_title = title
        self.current_section_lines = []

    def _flush_section(self) -> None:
        if self.current_section_title is None:
            return
        lines = [
            self.current_section_title,
            *self.current_section_lines,
        ]
        text = normalize_text("\n".join(lines))
        if len(text) >= 40:
            order_index = len(self.sections)
            section_key = section_key_for_title(self.current_section_title, order_index)
            self.sections.append(
                ParsedSection(
                    section_key=section_key,
                    citation=None,
                    title=self.current_section_title,
                    text=text,
                    order_index=order_index,
                )
            )
        self.current_section_title = None
        self.current_section_lines = []

    def title(self) -> str:
        for title in self.title_parts:
            if title:
                return title
        return self.fallback_title


def download_hpd_guidance_bundle(
    source_url: str,
    *,
    progress: Callable[[str], None] | None = None,
) -> DownloadedArtifact:
    urls = guidance_urls(source_url)
    settings = get_settings()
    timeout_seconds = min(
        settings.ingestion_http_timeout_seconds,
        HPD_GUIDANCE_PAGE_TIMEOUT_SECONDS,
    )
    max_retries = min(
        settings.ingestion_http_max_retries,
        HPD_GUIDANCE_PAGE_MAX_RETRIES,
    )
    pages: list[HpdGuidancePage] = []
    for index, url in enumerate(urls, start=1):
        if progress:
            progress(f"Downloading HPD guidance page {index}/{len(urls)}: {url}")
        try:
            artifact = download_url(
                url,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to download HPD guidance page "
                f"{index}/{len(urls)} ({url}). "
                "Try again, or ingest an official saved artifact."
            ) from exc
        html = artifact.content.decode("utf-8", errors="replace")
        pages.append(
            HpdGuidancePage(
                url=url,
                title=extract_guidance_title(html, url),
                html=html,
            )
        )
    content = hpd_guidance_bundle_bytes(pages)
    return DownloadedArtifact(
        content=content,
        content_hash=hash_bytes(content),
        content_type="application/json",
        byte_size=len(content),
        extension="json",
        source_url=source_url,
    )


def guidance_urls(source_url: str) -> list[str]:
    urls = [source_url, *HPD_GUIDANCE_URLS]
    seen: set[str] = set()
    output: list[str] = []
    for url in urls:
        if url in seen:
            continue
        if not url.startswith(HPD_GUIDANCE_ALLOWED_PREFIX):
            raise ValueError(f"HPD guidance URL is outside allowed prefix: {url}")
        seen.add(url)
        output.append(url)
    return output


def hpd_guidance_bundle_bytes(pages: list[HpdGuidancePage]) -> bytes:
    payload = {
        "format": HPD_BUNDLE_FORMAT,
        "pages": [
            {
                "url": page.url,
                "title": page.title,
                "retrieved_at": "tracked_by_source_version",
                "html": page.html,
            }
            for page in pages
        ],
    }
    return json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")


def parse_hpd_guidance_bundle_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
    content: bytes,
) -> tuple[int, int, int]:
    pages = pages_from_bundle(content)
    created = 0
    updated = 0
    skipped = 0
    for page in pages:
        page_created, page_updated, page_skipped = parse_hpd_guidance_page_document(
            db,
            source,
            source_version,
            page,
        )
        created += page_created
        updated += page_updated
        skipped += page_skipped
    db.commit()
    return created, updated, skipped


def parse_hpd_guidance_html_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
    content: bytes,
) -> tuple[int, int, int]:
    html = content.decode("utf-8", errors="replace")
    page = HpdGuidancePage(
        url=source_version.source_url or source.source_url,
        title=extract_guidance_title(html, source.name),
        html=html,
    )
    created, updated, skipped = parse_hpd_guidance_page_document(
        db,
        source,
        source_version,
        page,
    )
    db.commit()
    return created, updated, skipped


def pages_from_bundle(content: bytes) -> list[HpdGuidancePage]:
    payload = json.loads(content.decode("utf-8"))
    if payload.get("format") != HPD_BUNDLE_FORMAT:
        raise ValueError("Unsupported HPD guidance bundle format.")
    pages = []
    for page in payload.get("pages", []):
        url = page.get("url")
        html = page.get("html")
        if not url or not html:
            continue
        pages.append(
            HpdGuidancePage(
                url=url,
                title=page.get("title") or title_from_url(url),
                html=html,
            )
        )
    if not pages:
        raise ValueError("HPD guidance bundle did not contain pages.")
    return pages


def parse_hpd_guidance_page_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
    page: HpdGuidancePage,
) -> tuple[int, int, int]:
    sections = parse_hpd_guidance_page(page.url, page.title, page.html)
    document_key = document_key_for_url(page.url)
    document = db.scalar(
        select(Document).where(
            Document.source_version_id == source_version.id,
            Document.document_key == document_key,
        )
    )
    document_created = document is None
    if document is None:
        document = Document(
            source_id=source.id,
            source_version_id=source_version.id,
            document_key=document_key,
            title=page.title,
            document_type="guidance",
            jurisdiction=source.jurisdiction,
            source_url=page.url,
        )
        db.add(document)
        db.flush()
    else:
        document.title = page.title
        document.source_url = page.url

    created = 1 if document_created else 0
    updated = 0
    skipped = 0
    for parsed_section in sections:
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
                citation=None,
                title=parsed_section.title,
                hierarchy_path=parsed_section.section_key,
                order_index=parsed_section.order_index,
                text=parsed_section.text,
            )
            db.add(section)
            db.flush()
        else:
            section.title = parsed_section.title
            section.text = parsed_section.text
            section.order_index = parsed_section.order_index
            updated += 1

        chunk_key = f"{document_key}-{parsed_section.section_key}"
        text_hash = hash_bytes(normalize_text(parsed_section.text).encode("utf-8"))
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
                chunk_type="guidance_section",
                citation=None,
                title=parsed_section.title,
                text=parsed_section.text,
                text_hash=text_hash,
                order_index=parsed_section.order_index,
            )
            db.add(chunk)
            db.flush()
        else:
            chunk.title = parsed_section.title
            chunk.text = parsed_section.text
            chunk.text_hash = text_hash
            chunk.order_index = parsed_section.order_index
            updated += 1

        if section_created:
            created += 1
        if chunk_created:
            created += 1
    if not sections:
        skipped += 1
    return created, updated, skipped


def parse_hpd_guidance_page(
    url: str,
    fallback_title: str,
    html: str,
) -> list[ParsedSection]:
    content_parser = HpdGuidanceContentParser(
        fallback_title=fallback_title or title_from_url(url)
    )
    content_parser.feed(html)
    content_parser.close()
    if content_parser.sections:
        return content_parser.sections

    parser = HpdGuidanceParser(fallback_title=fallback_title or title_from_url(url))
    parser.feed(html)
    parser.close()
    if not parser.sections:
        text = normalize_text("\n".join(extract_plain_lines(html)))
        if text:
            return [
                ParsedSection(
                    section_key="overview",
                    citation=None,
                    title=fallback_title or title_from_url(url),
                    text=text,
                    order_index=0,
                )
            ]
    return parser.sections


def extract_guidance_title(html: str, fallback: str) -> str:
    parser = HpdGuidanceParser(fallback_title=fallback)
    parser.feed(html)
    parser.close()
    title = parser.title()
    return title.split(" - HPD", 1)[0].strip() or fallback


def extract_plain_lines(html: str) -> list[str]:
    parser = PlainGuidanceTextParser()
    parser.feed(html)
    parser.close()
    return parser.lines


class PlainGuidanceTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag.lower() in SKIP_TAGS or has_skip_attr(attrs):
            self.skip_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        line = clean_guidance_line(data)
        if line:
            self.lines.append(line)


def clean_guidance_line(value: str) -> str:
    line = re.sub(r"\s+", " ", value).strip()
    if not line:
        return ""
    if is_page_chrome_line(line) or line in HPD_CHROME_LINES:
        return ""
    if line.startswith("© City of New York"):
        return ""
    return line


def has_skip_attr(attrs) -> bool:
    for name, value in attrs:
        if name in {"class", "id", "role", "aria-label"} and value:
            if SKIP_ATTR_PATTERN.search(value):
                return True
    return False


def is_hpd_content_container(attrs) -> bool:
    for name, value in attrs:
        if name == "class" and value:
            classes = set(value.split())
            if "about-description" in classes:
                return True
    return False


class HpdGuidanceContentParser(HTMLParser):
    def __init__(self, fallback_title: str) -> None:
        super().__init__()
        self.fallback_title = fallback_title
        self.capture_depth = 0
        self.current_section_title: str | None = None
        self.current_section_lines: list[str] = []
        self.sections: list[ParsedSection] = []
        self.current_block_tag: str | None = None
        self.current_block_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if self.capture_depth == 0:
            if is_hpd_content_container(attrs):
                self.capture_depth = 1
            return

        self.capture_depth += 1
        if tag in HEADING_TAGS or tag in CONTENT_BLOCK_TAGS:
            self._flush_block()
            self.current_block_tag = tag
            self.current_block_parts = []
        elif tag == "br" and self.current_block_tag:
            self.current_block_parts.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if self.capture_depth and tag.lower() == "br" and self.current_block_tag:
            self.current_block_parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.capture_depth == 0:
            return
        if self.current_block_tag == tag:
            self._flush_block()
        self.capture_depth -= 1
        if self.capture_depth == 0:
            self._flush_block()
            self._flush_section()

    def handle_data(self, data: str) -> None:
        if self.capture_depth == 0 or self.current_block_tag is None:
            return
        if data.strip():
            self.current_block_parts.append(data)

    def close(self) -> None:
        self._flush_block()
        self._flush_section()
        super().close()

    def _flush_block(self) -> None:
        if self.current_block_tag is None:
            return
        text = clean_guidance_line(" ".join(self.current_block_parts))
        tag = self.current_block_tag
        self.current_block_tag = None
        self.current_block_parts = []
        if not text:
            return
        if tag in HEADING_TAGS:
            self._start_section(text)
            return
        if self.current_section_title is None:
            self._start_section(self.fallback_title)
        self.current_section_lines.append(text)

    def _start_section(self, title: str) -> None:
        self._flush_section()
        self.current_section_title = title
        self.current_section_lines = []

    def _flush_section(self) -> None:
        if self.current_section_title is None:
            return
        lines = [
            self.current_section_title,
            *self.current_section_lines,
        ]
        text = normalize_text("\n".join(lines))
        if len(text) >= 40:
            order_index = len(self.sections)
            section_key = section_key_for_title(self.current_section_title, order_index)
            self.sections.append(
                ParsedSection(
                    section_key=section_key,
                    citation=None,
                    title=self.current_section_title,
                    text=text,
                    order_index=order_index,
                )
            )
        self.current_section_title = None
        self.current_section_lines = []


def document_key_for_url(url: str) -> str:
    parsed = urlparse(url)
    stem = parsed.path.rsplit("/", 1)[-1].removesuffix(".page")
    slug = slugify(stem or "hpd-guidance")
    suffix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:120]}-{suffix}"


def section_key_for_title(title: str, order_index: int) -> str:
    slug = slugify(title)
    if not slug:
        slug = f"section-{order_index}"
    suffix = hashlib.sha1(f"{order_index}:{title}".encode()).hexdigest()[:6]
    return f"{slug[:100]}-{suffix}"


def title_from_url(url: str) -> str:
    stem = urlparse(url).path.rsplit("/", 1)[-1].removesuffix(".page")
    if not stem:
        return "HPD Guidance"
    return " ".join(part.capitalize() for part in stem.split("-"))


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
