import argparse
import json
import sys

from sqlalchemy import func, select

from app.answer.providers import get_answer_provider
from app.answer.service import generate_answer
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.document import Document
from app.models.source import Source
from app.models.source_version import SourceVersion
from app.models.user import User
from app.retrieval.hybrid import hybrid_search
from app.retrieval.schemas import SearchFilters, SearchResult


def answer_debug_command(
    user_email: str,
    question: str,
    limit: int,
    filters_json: str | None = None,
) -> None:
    filters = SearchFilters.from_dict(parse_filters(filters_json))
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email.lower()))
        if user is None:
            raise ValueError(f"User not found: {user_email}")

        provider = get_answer_provider()
        retrieved_results = hybrid_search(db, question, filters, limit)
        answer = generate_answer(db, user, question, filters, limit)

    print(
        json.dumps(
            {
                "provider": provider.provider_name,
                "model": provider.model_name,
                "question": question,
                "answer_status": answer.answer_status,
                "answer": answer.answer,
                "cited_chunk_ids": [
                    citation.chunk_id for citation in answer.citations
                ],
                "source_coverage_present": answer.source_coverage is not None,
                "disclaimer_present": bool(answer.disclaimer),
                "unsupported_reason": (
                    answer.answer if answer.answer_status == "unsupported" else None
                ),
                "retrieved_chunks": [
                    debug_chunk_payload(index, result)
                    for index, result in enumerate(retrieved_results, start=1)
                ],
            },
            sort_keys=True,
        )
    )


def source_debug_command(source_slug: str, samples: int) -> None:
    with SessionLocal() as db:
        source = db.scalar(select(Source).where(Source.slug == source_slug))
        if source is None:
            raise ValueError(f"Unknown source slug: {source_slug}")

        source_version = current_source_version(db, source)
        if source_version is None:
            raise ValueError(f"No source version found for {source_slug}")

        document_count = (
            db.scalar(
                select(func.count())
                .select_from(Document)
                .where(Document.source_version_id == source_version.id)
            )
            or 0
        )
        rows = db.execute(
            select(Chunk, Document.source_url)
            .join(Document, Document.id == Chunk.document_id)
            .where(Chunk.source_version_id == source_version.id)
            .order_by(Document.title, Chunk.order_index, Chunk.id)
        ).all()

    lengths = [len(chunk.text or "") for chunk, _source_url in rows]
    title_only_rows = [
        (chunk, source_url)
        for chunk, source_url in rows
        if is_title_only_chunk(chunk)
    ]
    sample_rows = title_only_rows[:samples] or rows[:samples]
    print(
        json.dumps(
            {
                "source_slug": source.slug,
                "source_version_id": source_version.id,
                "source_version_hash": source_version.content_hash,
                "document_count": document_count,
                "chunk_count": len(rows),
                "title_only_chunk_count": len(title_only_rows),
                "text_length_min": min(lengths) if lengths else 0,
                "text_length_max": max(lengths) if lengths else 0,
                "text_length_avg": (
                    round(sum(lengths) / len(lengths), 2) if lengths else 0
                ),
                "sample_chunks": [
                    source_chunk_payload(chunk, source_url)
                    for chunk, source_url in sample_rows
                ],
            },
            sort_keys=True,
        )
    )


def current_source_version(db, source: Source) -> SourceVersion | None:
    current = db.scalar(
        select(SourceVersion)
        .where(
            SourceVersion.source_id == source.id,
            SourceVersion.is_current.is_(True),
        )
        .order_by(SourceVersion.retrieved_at.desc())
    )
    if current is not None:
        return current
    return db.scalar(
        select(SourceVersion)
        .where(SourceVersion.source_id == source.id)
        .order_by(SourceVersion.retrieved_at.desc())
    )


def parse_filters(filters_json: str | None) -> dict | None:
    if not filters_json:
        return None
    try:
        parsed = json.loads(filters_json)
    except json.JSONDecodeError as exc:
        raise ValueError("filters-json must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError("filters-json must decode to a JSON object.")
    return parsed


def debug_chunk_payload(rank: int, result: SearchResult) -> dict:
    return {
        "rank": rank,
        "score": result.score,
        "match_type": result.match_type,
        "chunk_id": result.chunk_id,
        "citation": result.citation,
        "title": result.title,
        "source_name": result.source_name,
        "source_type": result.source_type,
        "source_url": result.source_url,
        "text_excerpt": excerpt(result.text),
    }


def excerpt(text: str, limit: int = 600) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def source_chunk_payload(chunk: Chunk, source_url: str) -> dict:
    return {
        "chunk_id": chunk.id,
        "title": chunk.title,
        "source_url": source_url,
        "text_length": len(chunk.text or ""),
        "title_only": is_title_only_chunk(chunk),
        "text_excerpt": excerpt(chunk.text or ""),
    }


def is_title_only_chunk(chunk: Chunk) -> bool:
    text = " ".join((chunk.text or "").split()).lower()
    title = " ".join((chunk.title or "").split()).lower()
    if not text:
        return True
    if len(text) < 50:
        return True
    return bool(title and text in {title, f"{title} - hpd"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run diagnostic commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    answer = subparsers.add_parser("answer")
    answer.add_argument("--user-email", required=True)
    answer.add_argument("--question", required=True)
    answer.add_argument("--limit", type=int, default=5)
    answer.add_argument("--filters-json")

    source = subparsers.add_parser("source")
    source.add_argument("--source-slug", required=True)
    source.add_argument("--samples", type=int, default=5)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "answer":
            answer_debug_command(
                args.user_email,
                args.question,
                args.limit,
                args.filters_json,
            )
        elif args.command == "source":
            source_debug_command(args.source_slug, args.samples)
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
