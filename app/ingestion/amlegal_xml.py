import re
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from sqlalchemy.orm import Session as DbSession

from app.ingestion.citations import normalize_citation
from app.ingestion.legal_text import (
    ParsedSection,
    normalize_text,
    parse_legal_sections,
)
from app.models.source import Source
from app.models.source_version import SourceVersion

AMLEGAL_NYC_ADMIN_XML_ZIP_URL = (
    "https://files.amlegal.com/pdffiles/NewYorkCity/Admin/XML.zip"
)
HMC_XML_MEMBER = "XML/0-0-0-60027.xml"
SECTION_STYLE_NAME = "Section"
HMC_HEADING_PATTERN = re.compile(
    r"^§+\s*(?P<section_number>27-\d{3,5})\s+(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
NYC_ADMIN_HEADING_PATTERN = re.compile(
    r"^§+\s*(?P<section_number>\d{2}-\d{3,5}(?:\.\d+)?)\.?\s+"
    r"(?P<title>.+?)\s*$",
    re.IGNORECASE,
)
MAX_XML_MEMBER_BYTES = 100 * 1024 * 1024
MAX_XML_TOTAL_BYTES = 500 * 1024 * 1024


def parse_hmc_bulk_xml_document(
    db: DbSession,
    source: Source,
    source_version: SourceVersion,
    zip_content: bytes,
) -> tuple[int, int, int]:
    xml_content = hmc_xml_from_zip(zip_content)
    parsed_sections = parse_hmc_xml_sections(xml_content)
    return parse_legal_sections(db, source, source_version, parsed_sections)


def hmc_xml_from_zip(zip_content: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(zip_content)) as archive:
        if HMC_XML_MEMBER not in archive.namelist():
            raise ValueError(f"AmLegal ZIP missing required member: {HMC_XML_MEMBER}")
        return archive.read(HMC_XML_MEMBER)


def parse_hmc_xml_sections(xml_content: bytes) -> list[ParsedSection]:
    return _parse_admin_xml_sections(xml_content, HMC_HEADING_PATTERN)


def parse_nyc_admin_xml_range_sections(
    zip_content: bytes,
    *,
    section_start: str,
    section_end: str,
) -> list[ParsedSection]:
    """Extract an inclusive Administrative Code range from the AmLegal bulk ZIP."""
    selected: dict[str, ParsedSection] = {}
    with zipfile.ZipFile(BytesIO(zip_content)) as archive:
        total_size = sum(item.file_size for item in archive.infolist())
        if total_size > MAX_XML_TOTAL_BYTES:
            raise ValueError("AmLegal XML ZIP exceeds the uncompressed size limit.")
        for item in archive.infolist():
            if item.is_dir() or not item.filename.lower().endswith(".xml"):
                continue
            if item.file_size > MAX_XML_MEMBER_BYTES:
                raise ValueError("AmLegal XML member exceeds the size limit.")
            try:
                candidates = _parse_admin_xml_sections(
                    archive.read(item),
                    NYC_ADMIN_HEADING_PATTERN,
                    require_sections=False,
                )
            except ElementTree.ParseError:
                continue
            for section in candidates:
                number = _section_number(section.citation)
                if number is None:
                    continue
                if (
                    _section_order(section_start)
                    <= _section_order(number)
                    <= _section_order(section_end)
                ):
                    current = selected.get(section.citation)
                    if current is None or len(section.text) > len(current.text):
                        selected[section.citation] = section
    if not selected:
        raise ValueError(
            "AmLegal XML did not contain the configured Administrative Code range."
        )
    return [
        ParsedSection(
            section_key=section.section_key,
            citation=section.citation,
            title=section.title,
            text=section.text,
            order_index=index,
        )
        for index, section in enumerate(
            sorted(
                selected.values(),
                key=lambda value: _section_order(
                    _section_number(value.citation) or section_end
                ),
            )
        )
    ]


def _parse_admin_xml_sections(
    xml_content: bytes,
    heading_pattern: re.Pattern[str],
    *,
    require_sections: bool = True,
) -> list[ParsedSection]:
    root = ElementTree.fromstring(xml_content)
    sections: list[ParsedSection] = []
    for level in root.iter("LEVEL"):
        if level.attrib.get("style-name") != SECTION_STYLE_NAME:
            continue
        heading = normalized_element_text(level.find("./RECORD/HEADING"))
        match = heading_pattern.match(heading)
        if match is None:
            continue
        section_number = match.group("section_number").upper()
        citation = normalize_citation(f"§ {section_number}")
        title = match.group("title").strip()
        paragraphs = section_paragraphs(level)
        if not paragraphs or paragraphs[0] != heading:
            paragraphs.insert(0, heading)
        text = normalize_text("\n".join(paragraphs))
        key = re.sub(r"[^a-z0-9]+", "-", citation.lower()).strip("-")
        sections.append(
            ParsedSection(
                section_key=key,
                citation=citation,
                title=title,
                text=text,
                order_index=len(sections),
            )
        )
    if not sections and require_sections:
        raise ValueError("AmLegal HMC XML did not contain citation-bearing sections.")
    return sections


def _section_order(section_number: str) -> tuple[int, int, tuple[int, ...]]:
    title, number = section_number.split("-", 1)
    parts = number.split(".")
    return int(title), int(parts[0]), tuple(int(part) for part in parts[1:])


def _section_number(citation: str) -> str | None:
    match = re.search(r"\b\d{2}-\d{3,5}(?:\.\d+)?\b", citation)
    return match.group(0) if match else None


def section_paragraphs(section_level: ElementTree.Element) -> list[str]:
    paragraphs: list[str] = []
    for record in section_level.iter("RECORD"):
        for paragraph in record.findall("PARA"):
            text = normalized_element_text(paragraph)
            if text:
                paragraphs.append(text)
    return paragraphs


def normalized_element_text(element: ElementTree.Element | None) -> str:
    if element is None:
        return ""
    return normalize_text(" ".join(part.strip() for part in element.itertext()))
