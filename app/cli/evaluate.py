import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.answer.service import generate_answer
from app.db.session import SessionLocal
from app.models.user import User
from app.retrieval.schemas import SearchFilters

DEFAULT_QUESTION_FILE = "tests/fixtures/evaluation_questions.json"


def evaluate_questions(question_file: str, user_email: str, limit: int) -> int:
    questions = json.loads(Path(question_file).read_text())
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == user_email.lower()))
        if user is None:
            print(f"User not found: {user_email}", file=sys.stderr)
            return 1
        for item in questions:
            question = item["question"]
            filters = SearchFilters.from_dict(item.get("filters"))
            answer = generate_answer(db, user, question, filters, limit)
            print(
                json.dumps(
                    {
                        "id": item.get("id"),
                        "question": question,
                        "answer_status": answer.answer_status,
                        "citation_count": len(answer.citations),
                    },
                    sort_keys=True,
                )
            )
    return 0


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
