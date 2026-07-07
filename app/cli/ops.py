import argparse
from datetime import timedelta

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.limits.time import utc_now
from app.models.answer_log import AnswerLog
from app.models.ingestion_run import IngestionRun
from app.models.rate_limit_event import RateLimitEvent
from app.models.retrieval_log import RetrievalLog


def log_summary_command(days: int) -> None:
    since = utc_now() - timedelta(days=days)
    with SessionLocal() as db:
        retrieval_count = (
            db.scalar(
                select(func.count())
                .select_from(RetrievalLog)
                .where(RetrievalLog.created_at >= since)
            )
            or 0
        )
        answer_counts = db.execute(
            select(AnswerLog.answer_status, func.count())
            .where(AnswerLog.created_at >= since)
            .group_by(AnswerLog.answer_status)
        ).all()
    print(f"retrieval_logs: {retrieval_count}")
    for status, count in answer_counts:
        print(f"answer_logs.{status}: {count}")


def rate_limit_summary_command(days: int) -> None:
    since = utc_now() - timedelta(days=days)
    with SessionLocal() as db:
        rows = db.execute(
            select(RateLimitEvent.event_type, func.count())
            .where(RateLimitEvent.created_at >= since)
            .group_by(RateLimitEvent.event_type)
        ).all()
    for event_type, count in rows:
        print(f"{event_type}: {count}")


def source_freshness_command() -> None:
    with SessionLocal() as db:
        rows = db.execute(
            select(
                IngestionRun.run_type,
                IngestionRun.status,
                func.max(IngestionRun.finished_at),
            ).group_by(IngestionRun.run_type, IngestionRun.status)
        ).all()
    for run_type, status, finished_at in rows:
        print(f"{run_type}.{status}: {finished_at}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run operational summaries.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    log_summary = subparsers.add_parser("log-summary")
    log_summary.add_argument("--days", type=int, default=1)

    rate_limits = subparsers.add_parser("rate-limits")
    rate_limits.add_argument("--days", type=int, default=1)

    subparsers.add_parser("source-freshness")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "log-summary":
        log_summary_command(args.days)
    elif args.command == "rate-limits":
        rate_limit_summary_command(args.days)
    elif args.command == "source-freshness":
        source_freshness_command()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
