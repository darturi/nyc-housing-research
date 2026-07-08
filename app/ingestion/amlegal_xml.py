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
    root = ElementTree.fromstring(xml_content)
    sections: list[ParsedSection] = []
    for level in root.iter("LEVEL"):
        if level.attrib.get("style-name") != SECTION_STYLE_NAME:
            continue
        heading = normalized_element_text(level.find("./RECORD/HEADING"))
        match = HMC_HEADING_PATTERN.match(heading)
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
    if not sections:
        raise ValueError("AmLegal HMC XML did not contain citation-bearing sections.")
    return sections


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
