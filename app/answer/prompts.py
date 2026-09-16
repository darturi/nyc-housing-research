import re

from app.answer.schemas import PromptContext
from app.retrieval.schemas import SearchResult

LEGAL_INFORMATION_DISCLAIMER = (
    "This is legal information, not legal advice. Consult a qualified attorney "
    "for advice about a specific situation."
)

LOCAL_ANSWER_PROMPT_VERSION = "local-answer-v1"

DEFAULT_SOURCE_COVERAGE = (
    "This MVP searched the configured public NYC housing-law corpus. It does "
    "not include paid legal databases, proprietary case-law collections, "
    "unpublished materials, or sources that have not been ingested."
)

PERSONAL_SCENARIO_INSTRUCTION = (
    "The question appears to concern the reader's own eviction, Housing Court, "
    "or likely case outcome. Give only general, cited legal information and "
    "identify facts or documents the reader can gather. Do not predict whether "
    "the reader will win or lose, state what will happen in their case, or give "
    "tailored legal advice."
)

PERSONAL_SCENARIO_PATTERN = re.compile(
    r"\b(my|me|i|we|our)\b.*\b(evict|eviction|housing\s+court|court\s+case|"
    r"nonpayment|holdover|landlord)\b|\b(will|can)\s+i\s+(win|lose|be\s+evicted)",
    re.IGNORECASE,
)


def is_personal_housing_scenario(question: str) -> bool:
    return bool(PERSONAL_SCENARIO_PATTERN.search(question))


def trim_context(
    results: list[SearchResult],
    max_chunks: int,
    max_chars: int,
) -> list[SearchResult]:
    selected: list[SearchResult] = []
    used_chars = 0
    for result in results[:max_chunks]:
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        text = result.text
        if len(text) > remaining:
            text = text[:remaining].rstrip()
        selected.append(
            SearchResult(
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                source_id=result.source_id,
                source_version_id=result.source_version_id,
                source_name=result.source_name,
                source_type=result.source_type,
                jurisdiction=result.jurisdiction,
                source_url=result.source_url,
                citation=result.citation,
                title=result.title,
                text=text,
                score=result.score,
                match_type=result.match_type,
            )
        )
        used_chars += len(text)
    return selected


def build_prompt(context: PromptContext) -> str:
    chunk_blocks = []
    for index, chunk in enumerate(context.chunks, start=1):
        citation = chunk.citation or "No formal citation"
        title = chunk.title or "Untitled"
        chunk_blocks.append(
            "\n".join(
                [
                    f"[Chunk {index}]",
                    f"chunk_id: {chunk.chunk_id}",
                    f"citation: {citation}",
                    f"title: {title}",
                    f"source_name: {chunk.source_name}",
                    f"source_url: {chunk.source_url}",
                    f"text: {chunk.text}",
                ]
            )
        )
    source_coverage = context.source_coverage or "No source coverage statement."
    return "\n\n".join(
        [
            "You are a citation-grounded NYC housing-law information tool.",
            "Do not provide legal advice. Use only the retrieved chunks below.",
            "If the chunks do not support an answer, say the current corpus does "
            "not contain enough retrieved public-source material to answer.",
            "Return reader-friendly prose in the answer field. Put citations only "
            "in cited_chunk_ids; do not include chunk IDs, a citations section, "
            "URLs, or a disclaimer in the answer text.",
            "Return a direct answer, relevant law or source-backed rule, practical "
            "implications if supported, and exceptions or limits if supported.",
            (
                PERSONAL_SCENARIO_INSTRUCTION
                if is_personal_housing_scenario(context.question)
                else ""
            ),
            f"Question: {context.question}",
            f"Source coverage: {source_coverage}",
            f"Disclaimer: {context.disclaimer}",
            "Retrieved chunks:",
            "\n\n".join(chunk_blocks) if chunk_blocks else "None",
        ]
    )
