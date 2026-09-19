from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.corpus.service import CorpusValidationError
from app.ingestion.legal_text import normalize_text

MAX_RESOURCE_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_EXTRACTED_CHARACTERS = 2_000_000
MAX_RESOURCE_CHUNKS = 5_000
TARGET_CHUNK_CHARACTERS = 2_500
MAX_CHUNK_CHARACTERS = 4_000
SUPPORTED_MEDIA_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
}


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
    elif suffix == ".md":
        detected = "text/markdown"
    elif suffix == ".txt":
        detected = "text/plain"
    else:
        detected = normalized
    if detected not in SUPPORTED_MEDIA_TYPES:
        raise CorpusValidationError(
            "Unsupported resource type. Use a text PDF, .txt, or .md file."
        )
    if detected == "application/pdf" and not content.startswith(b"%PDF-"):
        raise CorpusValidationError(
            "The selected PDF does not have a valid PDF header."
        )
    if detected != "application/pdf" and content.startswith(b"%PDF-"):
        detected = "application/pdf"
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
    return _parse_text(content, title=title, media_type=detected)


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
        total_characters += len(normalized)
        _check_character_limit(total_characters)
        page_chunks = _chunk_text(
            normalized,
            title=title,
            locator_base={"kind": "pdf_page", "pdf_page": page_number},
            starting_ordinal=ordinal,
        )
        chunks.extend(page_chunks)
        ordinal += len(page_chunks)
        _check_chunk_limit(chunks)
    if not chunks:
        raise CorpusValidationError(
            "No text could be extracted from the PDF. Scanned PDFs require OCR, "
            "which is not supported yet."
        )
    return ParsedResource(
        media_type="application/pdf",
        chunks=tuple(chunks),
        page_count=len(reader.pages),
        extracted_characters=total_characters,
        warnings=tuple(warnings),
    )


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
