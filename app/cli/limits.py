import argparse
import sys

from sqlalchemy import select

from app.cli.users import normalize_email
from app.db.session import SessionLocal
from app.limits.service import prune_old_events, reset_user_limit_state
from app.models.user import User


def prune_events_command() -> None:
    with SessionLocal() as db:
        deleted = prune_old_events(db)
    print(f"Deleted {deleted} old rate limit events.")


def reset_user_command(email: str) -> None:
    normalized_email = normalize_email(email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == normalized_email))
        if user is None:
            raise ValueError(f"User not found: {normalized_email}")
        rate_limit_events_deleted, answer_logs_deleted = reset_user_limit_state(
            db,
            user.id,
        )
    print(
        f"Reset limits for {normalized_email}: "
        f"deleted {rate_limit_events_deleted} rate limit events and "
        f"{answer_logs_deleted} answer logs from today's UTC budget window."
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run rate limit maintenance.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prune-events")

    reset_user = subparsers.add_parser("reset-user")
    reset_user.add_argument("--email", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "prune-events":
            prune_events_command()
        elif args.command == "reset-user":
            reset_user_command(args.email)
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
