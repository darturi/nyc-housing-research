from __future__ import annotations

import re
import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.corpus.service import CorpusValidationError
from app.ingestion.legal_text import normalize_text

MAX_RESOURCE_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARACTERS = 2_000_000
MAX_RESOURCE_CHUNKS = 5_000
MAX_DOCX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_DOCX_MEMBERS = 10_000
MAX_DOCX_EXPANSION_RATIO = 1_000
TARGET_CHUNK_CHARACTERS = 2_500
MAX_CHUNK_CHARACTERS = 4_000
SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": WORD_NS, "r": REL_NS}


@dataclass(frozen=True)
class ResourceChunk:
    stable_id: str
    title: str
    text: str
    locator: dict[str, object]


@dataclass(frozen=True)
class ParsedResource:
    media_type: str
    chunks: tuple[ResourceChunk, ...]
    page_count: int | None
    extracted_characters: int
    warnings: tuple[str, ...]


def detect_resource_media_type(
    filename: str,
    supplied_media_type: str | None,
    content: bytes,
) -> str:
    suffix = Path(filename).suffix.lower()
    normalized = (supplied_media_type or "").split(";", 1)[0].strip().lower()
    if content.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif suffix == ".docx" or normalized == DOCX_MEDIA_TYPE:
        detected = DOCX_MEDIA_TYPE
    elif suffix == ".md":
        detected = "text/markdown"
    elif suffix == ".txt":
        detected = "text/plain"
    else:
        detected = normalized
    if detected not in SUPPORTED_MEDIA_TYPES:
        raise CorpusValidationError(
            "Unsupported resource type. Use a PDF, DOCX, .txt, or .md file."
        )
    if detected == "application/pdf" and not content.startswith(b"%PDF-"):
        raise CorpusValidationError(
            "The selected PDF does not have a valid PDF header."
        )
    if detected != "application/pdf" and content.startswith(b"%PDF-"):
        detected = "application/pdf"
    if detected == DOCX_MEDIA_TYPE and not content.startswith(b"PK"):
        raise CorpusValidationError("The selected DOCX is not a valid OOXML package.")
    return detected


def parse_resource(
    content: bytes,
    *,
    filename: str,
    media_type: str | None,
    title: str,
) -> ParsedResource:
    if not content:
        raise CorpusValidationError("The selected resource is empty.")
    if len(content) > MAX_RESOURCE_BYTES:
        raise CorpusValidationError(
            f"Resource exceeds the {MAX_RESOURCE_BYTES // (1024 * 1024)} MiB limit."
        )
    detected = detect_resource_media_type(filename, media_type, content)
    if detected == "application/pdf":
        return _parse_pdf(content, title=title)
    if detected == DOCX_MEDIA_TYPE:
        return _parse_docx(content, title=title)
    return _parse_text(content, title=title, media_type=detected)


def inspect_pdf(content: bytes) -> dict[str, object]:
    """Return a bounded per-page embedded-text assessment without running OCR."""
    if not content.startswith(b"%PDF-"):
        raise CorpusValidationError("The selected PDF does not have a valid header.")
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except (PdfReadError, ValueError, OSError) as exc:
        raise CorpusValidationError(
            "The PDF is malformed or could not be read."
        ) from exc
    if reader.is_encrypted:
        raise CorpusValidationError("Encrypted PDFs are not supported.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise CorpusValidationError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit.")
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            extracted = normalize_text(page.extract_text() or "")
        except Exception:
            pages.append(
                {
                    "physical_page": page_number,
                    "embedded_characters": None,
                    "status": "inspection_failed",
                    "ocr_candidate": True,
                }
            )
            continue
        character_count = len(extracted)
        pages.append(
            {
                "physical_page": page_number,
                "embedded_characters": character_count,
                "status": (
                    "embedded_text"
                    if character_count >= 20
                    else "sparse_embedded_text"
                    if character_count
                    else "no_embedded_text"
                ),
                "ocr_candidate": character_count < 20,
            }
        )
    return {
        "page_count": len(pages),
        "pages": pages,
        "embedded_text_pages": sum(item["status"] == "embedded_text" for item in pages),
        "ocr_candidate_pages": sum(bool(item["ocr_candidate"]) for item in pages),
        "ocr_runtime": "unavailable",
        "ocr_runtime_reason": (
            "No reviewed, pinned local OCR engine and language package is bundled."
        ),
    }


def _parse_pdf(content: bytes, *, title: str) -> ParsedResource:
    try:
        reader = PdfReader(BytesIO(content), strict=True)
    except (PdfReadError, ValueError, OSError) as exc:
        raise CorpusValidationError(
            "The PDF is malformed or could not be read."
        ) from exc
    if reader.is_encrypted:
        raise CorpusValidationError("Encrypted PDFs are not supported.")
    if len(reader.pages) > MAX_PDF_PAGES:
        raise CorpusValidationError(f"PDF exceeds the {MAX_PDF_PAGES}-page limit.")
    chunks: list[ResourceChunk] = []
    warnings: list[str] = []
    total_characters = 0
    ordinal = 0
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:
            raise CorpusValidationError(
                f"Text extraction failed on PDF page {page_number}."
            ) from exc
        normalized = normalize_text(raw)
        if not normalized:
            warnings.append(f"PDF page {page_number} has no extractable text.")
            continue
        quality_status = "review_recommended" if len(normalized) < 20 else "usable"
        if quality_status != "usable":
            warnings.append(
                f"PDF page {page_number} has sparse embedded text; review or OCR "
                "may be needed."
            )
        total_characters += len(normalized)
        _check_character_limit(total_characters)
        page_chunks = _chunk_text(
            normalized,
            title=title,
            locator_base={
                "kind": "pdf_page",
                "pdf_page": page_number,
                "extraction_method": "embedded_text",
                "quality_status": quality_status,
                "derivative_revision": 1,
            },
            starting_ordinal=ordinal,
        )
        chunks.extend(page_chunks)
        ordinal += len(page_chunks)
        _check_chunk_limit(chunks)
    if not chunks:
        raise CorpusValidationError(
            "No text could be extracted from the PDF. Scanned PDFs require OCR, "
            "but no reviewed local OCR runtime is currently bundled."
        )
    return ParsedResource(
        media_type="application/pdf",
        chunks=tuple(chunks),
        page_count=len(reader.pages),
        extracted_characters=total_characters,
        warnings=tuple(warnings),
    )


def _parse_docx(content: bytes, *, title: str) -> ParsedResource:
    members, warnings = _validated_docx_members(content)
    document = _xml(members["word/document.xml"], "DOCX main document")
    style_names = _docx_style_names(members)
    chunks: list[ResourceChunk] = []
    headings: list[str] = []
    ordinal = 0
    paragraph_number = 0
    table_number = 0
    body = document.find("w:body", NS)
    if body is None:
        raise CorpusValidationError("The DOCX main document has no body.")
    if document.findall(".//w:ins", NS) or document.findall(".//w:del", NS):
        warnings.append(
            "Tracked revisions were present; extraction uses visible final-view text "
            "(insertions included, deletions excluded)."
        )
    if "word/comments.xml" in members:
        warnings.append("DOCX comments were present and excluded from extraction.")
    total_characters = 0
    for element in body:
        local = _local_name(element.tag)
        if local == "p":
            paragraph_number += 1
            text_value = normalize_text(_visible_word_text(element))
            if not text_value:
                continue
            style_id = _paragraph_style(element)
            style_name = style_names.get(style_id, style_id or "")
            if style_name.casefold().startswith("heading"):
                try:
                    level = int(re.search(r"\d+", style_name).group())
                except (AttributeError, ValueError):
                    level = 1
                headings[:] = headings[: max(0, level - 1)]
                headings.append(text_value)
            ordinal += 1
            total_characters += len(text_value)
            chunks.append(
                ResourceChunk(
                    stable_id=f"docx-p-{paragraph_number:05d}",
                    title=title,
                    text=text_value,
                    locator={
                        "kind": "docx_paragraph",
                        "paragraph": paragraph_number,
                        "heading_path": list(headings),
                        "style": style_name or None,
                        "chunk_ordinal": ordinal,
                        "extraction_method": "docx_ooxml",
                        "quality_status": "usable",
                        "derivative_revision": 1,
                    },
                )
            )
        elif local == "tbl":
            table_number += 1
            for row_number, row in enumerate(element.findall("w:tr", NS), start=1):
                for cell_number, cell in enumerate(row.findall("w:tc", NS), start=1):
                    text_value = normalize_text(_visible_word_text(cell))
                    if not text_value:
                        continue
                    ordinal += 1
                    total_characters += len(text_value)
                    chunks.append(
                        ResourceChunk(
                            stable_id=(
                                f"docx-t-{table_number:04d}-r-{row_number:04d}-"
                                f"c-{cell_number:04d}"
                            ),
                            title=title,
                            text=text_value,
                            locator={
                                "kind": "docx_table_cell",
                                "table": table_number,
                                "row": row_number,
                                "cell": cell_number,
                                "heading_path": list(headings),
                                "chunk_ordinal": ordinal,
                                "extraction_method": "docx_ooxml",
                                "quality_status": "usable",
                                "derivative_revision": 1,
                            },
                        )
                    )
        _check_character_limit(total_characters)
        _check_chunk_limit(chunks)
    for note_kind in ("footnotes", "endnotes"):
        member = f"word/{note_kind}.xml"
        if member not in members:
            continue
        root = _xml(members[member], f"DOCX {note_kind}")
        singular = "footnote" if note_kind == "footnotes" else "endnote"
        for note in root.findall(f"w:{singular}", NS):
            raw_id = note.attrib.get(f"{{{WORD_NS}}}id", "")
            if raw_id.startswith("-"):
                continue
            text_value = normalize_text(_visible_word_text(note))
            if not text_value:
                continue
            ordinal += 1
            total_characters += len(text_value)
            chunks.append(
                ResourceChunk(
                    stable_id=f"docx-{singular}-{raw_id or ordinal}",
                    title=title,
                    text=text_value,
                    locator={
                        "kind": f"docx_{singular}",
                        "note_id": raw_id or None,
                        "chunk_ordinal": ordinal,
                        "extraction_method": "docx_ooxml",
                        "quality_status": "usable",
                        "derivative_revision": 1,
                    },
                )
            )
            _check_character_limit(total_characters)
            _check_chunk_limit(chunks)
    if not chunks:
        raise CorpusValidationError("The selected DOCX contains no visible text.")
    return ParsedResource(
        media_type=DOCX_MEDIA_TYPE,
        chunks=tuple(chunks),
        page_count=None,
        extracted_characters=total_characters,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def _validated_docx_members(content: bytes) -> tuple[dict[str, bytes], list[str]]:
    warnings: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_DOCX_MEMBERS:
                raise CorpusValidationError(
                    f"DOCX exceeds the {MAX_DOCX_MEMBERS}-member limit."
                )
            expanded = 0
            members: dict[str, bytes] = {}
            for info in infos:
                path = PurePosixPath(info.filename)
                mode = info.external_attr >> 16
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in info.filename
                    or stat.S_ISLNK(mode)
                ):
                    raise CorpusValidationError("DOCX contains an unsafe package path.")
                expanded += info.file_size
                if expanded > MAX_DOCX_EXPANDED_BYTES:
                    raise CorpusValidationError(
                        "Expanded DOCX exceeds the 100 MiB safety limit."
                    )
                if (
                    info.file_size > 1_000_000
                    and info.compress_size > 0
                    and info.file_size / info.compress_size > MAX_DOCX_EXPANSION_RATIO
                ):
                    raise CorpusValidationError(
                        "DOCX contains a member with an unsafe expansion ratio."
                    )
                if info.is_dir():
                    continue
                lower = info.filename.casefold()
                if lower.endswith("vbaproject.bin"):
                    raise CorpusValidationError(
                        "Macro-enabled DOCX content is rejected."
                    )
                members[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, OSError) as exc:
        raise CorpusValidationError("The selected DOCX package is malformed.") from exc
    required = {"[Content_Types].xml", "word/document.xml"}
    if not required <= members.keys():
        raise CorpusValidationError("The selected file is not a complete DOCX package.")
    types = members["[Content_Types].xml"]
    if b"wordprocessingml.document.main+xml" not in types:
        raise CorpusValidationError(
            "The package does not declare a DOCX main document."
        )
    for name, payload in members.items():
        if name.endswith(".xml") or name.endswith(".rels"):
            if b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
                raise CorpusValidationError(
                    "DOCX XML entity declarations are rejected."
                )
        if name.endswith(".rels"):
            root = _xml(payload, "DOCX relationships")
            for relationship in root.findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
                if relationship.attrib.get("TargetMode") == "External":
                    warnings.append(
                        "External DOCX relationships were present and were not "
                        "followed."
                    )
    if any(name.startswith("word/embeddings/") for name in members):
        warnings.append("Embedded DOCX objects were present and were not executed.")
    return members, warnings


def _xml(content: bytes, label: str):
    try:
        return ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise CorpusValidationError(f"{label} XML is malformed.") from exc


def _docx_style_names(members: dict[str, bytes]) -> dict[str, str]:
    if "word/styles.xml" not in members:
        return {}
    root = _xml(members["word/styles.xml"], "DOCX styles")
    result = {}
    for style in root.findall("w:style", NS):
        style_id = style.attrib.get(f"{{{WORD_NS}}}styleId")
        name = style.find("w:name", NS)
        if style_id and name is not None:
            result[style_id] = name.attrib.get(f"{{{WORD_NS}}}val", style_id)
    return result


def _paragraph_style(paragraph) -> str | None:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    return style.attrib.get(f"{{{WORD_NS}}}val") if style is not None else None


def _visible_word_text(element) -> str:
    parts: list[str] = []

    def visit(node) -> None:
        local = _local_name(node.tag)
        if local in {"del", "commentReference", "drawing", "object"}:
            return
        if local in {"t", "instrText"} and node.text:
            parts.append(node.text)
        elif local in {"tab"}:
            parts.append("\t")
        elif local in {"br", "cr"}:
            parts.append("\n")
        for child in node:
            visit(child)
        if local == "p":
            parts.append("\n")

    visit(element)
    return "".join(parts)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_text(content: bytes, *, title: str, media_type: str) -> ParsedResource:
    if b"\x00" in content:
        raise CorpusValidationError("The selected text file appears to be binary.")
    try:
        raw = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CorpusValidationError("Text resources must use UTF-8 encoding.") from exc
    normalized = normalize_text(raw)
    if not normalized:
        raise CorpusValidationError("The selected resource contains no text.")
    _check_character_limit(len(normalized))
    chunks = _chunk_text(
        normalized,
        title=title,
        locator_base={"kind": "paragraph_range"},
        starting_ordinal=0,
    )
    _check_chunk_limit(chunks)
    return ParsedResource(
        media_type=media_type,
        chunks=tuple(chunks),
        page_count=None,
        extracted_characters=len(normalized),
        warnings=(),
    )


def _chunk_text(
    text: str,
    *,
    title: str,
    locator_base: dict[str, object],
    starting_ordinal: int,
) -> list[ResourceChunk]:
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    pieces: list[tuple[str, int, int]] = []
    for paragraph_number, paragraph in enumerate(paragraphs, start=1):
        if len(paragraph) <= MAX_CHUNK_CHARACTERS:
            pieces.append((paragraph, paragraph_number, paragraph_number))
            continue
        for offset in range(0, len(paragraph), TARGET_CHUNK_CHARACTERS):
            piece = paragraph[offset : offset + TARGET_CHUNK_CHARACTERS].strip()
            if piece:
                pieces.append((piece, paragraph_number, paragraph_number))

    grouped: list[tuple[str, int, int]] = []
    current: list[str] = []
    current_length = 0
    start = 0
    end = 0
    for piece, paragraph_start, paragraph_end in pieces:
        addition = len(piece) + (1 if current else 0)
        if current and current_length + addition > TARGET_CHUNK_CHARACTERS:
            grouped.append(("\n".join(current), start, end))
            current = []
            current_length = 0
        if not current:
            start = paragraph_start
        current.append(piece)
        current_length += addition
        end = paragraph_end
    if current:
        grouped.append(("\n".join(current), start, end))

    chunks = []
    for local_index, (chunk_text, paragraph_start, paragraph_end) in enumerate(grouped):
        ordinal = starting_ordinal + local_index + 1
        locator = dict(locator_base)
        locator.update(
            {
                "paragraph_start": paragraph_start,
                "paragraph_end": paragraph_end,
                "chunk_ordinal": ordinal,
            }
        )
        chunks.append(
            ResourceChunk(
                stable_id=f"chunk-{ordinal:05d}",
                title=title,
                text=chunk_text,
                locator=locator,
            )
        )
    return chunks


def _check_character_limit(count: int) -> None:
    if count > MAX_EXTRACTED_CHARACTERS:
        raise CorpusValidationError(
            "Extracted text exceeds the 2,000,000-character limit."
        )


def _check_chunk_limit(chunks: list[ResourceChunk]) -> None:
    if len(chunks) > MAX_RESOURCE_CHUNKS:
        raise CorpusValidationError(
            f"Resource exceeds the {MAX_RESOURCE_CHUNKS}-chunk limit."
        )
