import argparse
import sys

from app.db.session import SessionLocal
from app.limits.service import prune_old_events


def prune_events_command() -> None:
    with SessionLocal() as db:
        deleted = prune_old_events(db)
    print(f"Deleted {deleted} old rate limit events.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run rate limit maintenance.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prune-events")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "prune-events":
            prune_events_command()
        else:
            parser.error(f"Unknown command: {args.command}")
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
