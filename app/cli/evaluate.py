import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.answer.schemas import AnswerCitation, AnswerResult
from app.answer.service import generate_answer
from app.db.session import SessionLocal
from app.models.chunk import Chunk
from app.models.source import Source
from app.models.user import User
from app.retrieval.schemas import SearchFilters

DEFAULT_QUESTION_FILE = "tests/fixtures/evaluation_questions.json"


def evaluate_questions(question_file: str, user_email: str, limit: int) -> int:
    questions = json.loads(Path(question_file).read_text())
    failed = False
    failure_messages: list[str] = []
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email.lower()))
        if user is None:
            print(f"User not found: {user_email}", file=sys.stderr)
            return 1
        for item in questions:
            question = item["question"]
            filters = SearchFilters.from_dict(item.get("filters"))
            answer = generate_answer(db, user, question, filters, limit)
            citation_source_types = source_types_for_citations(
                db,
                answer.citations,
            )
            failures = evaluate_expectations(item, answer, citation_source_types)
            passed = not failures
            failed = failed or not passed
            if failures:
                item_id = item.get("id") or question
                for failure in failures:
                    failure_messages.append(f"{item_id}: {failure}")
            print(
                json.dumps(
                    {
                        "id": item.get("id"),
                        "question": question,
                        "answer_status": answer.answer_status,
                        "citation_count": len(answer.citations),
                        "passed": passed,
                        "failures": failures,
                    },
                    sort_keys=True,
                )
            )
    if failed:
        print("Evaluation failed.", file=sys.stderr)
        for failure_message in failure_messages:
            print(f"- {failure_message}", file=sys.stderr)
        return 1
    return 0


def evaluate_expectations(
    item: dict,
    answer: AnswerResult,
    citation_source_types: dict[str, str] | None = None,
) -> list[str]:
    failures: list[str] = []
    expected_status = item.get("expected_status")
    if expected_status and answer.answer_status != expected_status:
        failures.append(
            f"expected answer_status {expected_status!r}, got "
            f"{answer.answer_status!r}"
        )

    min_citation_count = item.get("min_citation_count", 0)
    if len(answer.citations) < min_citation_count:
        failures.append(
            f"expected at least {min_citation_count} citations, got "
            f"{len(answer.citations)}"
        )

    required_source_name_contains = item.get("required_source_name_contains")
    if required_source_name_contains and not citation_source_name_matches(
        answer.citations,
        required_source_name_contains,
    ):
        failures.append(
            "expected a citation source name containing "
            f"{required_source_name_contains!r}"
        )

    required_source_type = item.get("required_source_type")
    if required_source_type:
        source_types = citation_source_types or {}
        if required_source_type not in source_types.values():
            failures.append(
                f"expected a citation from source_type {required_source_type!r}"
            )

    required_citation_contains = item.get("required_citation_contains")
    if required_citation_contains and not citation_text_matches(
        answer.citations,
        required_citation_contains,
    ):
        failures.append(
            f"expected a citation containing {required_citation_contains!r}"
        )

    for term in item.get("required_answer_terms", []):
        if term.lower() not in answer.answer.lower():
            failures.append(f"expected answer to contain {term!r}")

    return failures


def citation_source_name_matches(
    citations: list[AnswerCitation],
    expected_substring: str,
) -> bool:
    expected = expected_substring.lower()
    return any(expected in citation.source_name.lower() for citation in citations)


def citation_text_matches(
    citations: list[AnswerCitation],
    expected_substring: str,
) -> bool:
    expected = expected_substring.lower()
    return any(
        expected in (citation.citation or "").lower() for citation in citations
    )


def source_types_for_citations(db, citations: list[AnswerCitation]) -> dict[str, str]:
    chunk_ids = [citation.chunk_id for citation in citations]
    if not chunk_ids:
        return {}
    rows = db.execute(
        select(Chunk.id, Source.source_type)
        .join(Source, Source.id == Chunk.source_id)
        .where(Chunk.id.in_(chunk_ids))
    ).all()
    return {chunk_id: source_type for chunk_id, source_type in rows}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate sample questions.")
    parser.add_argument("--question-file", default=DEFAULT_QUESTION_FILE)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--limit", type=int, default=5)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return evaluate_questions(args.question_file, args.user_email, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
