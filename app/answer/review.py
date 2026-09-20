"""Explicit, report-bound human review; no model or network calls."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REVIEW_DIMENSIONS = (
    "accuracy",
    "qualifications_and_missing_facts",
    "citation_support",
    "no_unsupported_claims",
)


def read_evaluation_report(path: Path) -> dict:
    payload = read_json(path)
    cases = payload.get("cases")
    if (
        payload.get("format") != "nyc-housing-answer-evaluation"
        or payload.get("format_version") != 1
        or not payload.get("evaluation_id")
        or not isinstance(cases, list)
        or not cases
        or any(
            not isinstance(case, dict)
            or not isinstance(case.get("case_id"), str)
            or not case["case_id"]
            for case in cases
        )
    ):
        raise ValueError("A versioned answer-evaluation report with cases is required.")
    ids = [case["case_id"] for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation report contains duplicate case IDs.")
    return payload


def report_digest(report: dict) -> str:
    content = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()


def create_review_template(report: dict) -> dict:
    return {
        "format": "nyc-housing-answer-review",
        "format_version": 1,
        "evaluation_id": report["evaluation_id"],
        "report_sha256": report_digest(report),
        "reviewer": "",
        "reviewer_role": "",
        "reviewed_at": "",
        "instructions": (
            "A housing-law reviewer must inspect each answer and its evidence. "
            "Set every dimension to pass or fail; leave unfinished work pending. "
            "Check required propositions and missing facts in the original report. "
            "Explain failures in notes. Record reviewer, role, and an ISO 8601 "
            "timestamp with timezone. These are self-reported review judgments."
        ),
        "cases": [
            {
                "case_id": case["case_id"],
                **{dimension: "pending" for dimension in REVIEW_DIMENSIONS},
                "notes": "",
            }
            for case in report["cases"]
        ],
    }


def assess_review(report: dict, review: dict) -> dict:
    if (
        review.get("format") != "nyc-housing-answer-review"
        or review.get("format_version") != 1
        or review.get("evaluation_id") != report["evaluation_id"]
        or review.get("report_sha256") != report_digest(report)
    ):
        raise ValueError("Review does not match this exact evaluation report.")
    cases = review.get("cases")
    if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
        raise ValueError("Review cases must be a list of case judgments.")
    ids = [c.get("case_id") for c in cases]
    if (
        any(not isinstance(case_id, str) for case_id in ids)
        or len(ids) != len(set(ids))
        or set(ids) != {c["case_id"] for c in report["cases"]}
    ):
        raise ValueError("Review must include each reported case exactly once.")
    pending, failed = [], []
    has_judgments = False
    for case in cases:
        judgments = [case.get(dimension) for dimension in REVIEW_DIMENSIONS]
        if any(value not in ("pending", "pass", "fail") for value in judgments):
            raise ValueError("Each review dimension must be pending, pass, or fail.")
        has_judgments |= any(value != "pending" for value in judgments)
        notes = case.get("notes", "")
        if not isinstance(notes, str):
            raise ValueError("Review notes must be text.")
        if "fail" in judgments:
            if not notes.strip():
                raise ValueError("Failed review cases require explanatory notes.")
            failed.append(case["case_id"])
        if "pending" in judgments:
            pending.append(case["case_id"])
    if has_judgments:
        for field in ("reviewer", "reviewer_role", "reviewed_at"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                raise ValueError("Record the reviewer, role, and review timestamp.")
        try:
            reviewed_at = datetime.fromisoformat(review["reviewed_at"])
            if reviewed_at.utcoffset() is None:
                raise ValueError("timezone missing")
        except ValueError as exc:
            raise ValueError(
                "Review timestamp must use ISO 8601 with timezone."
            ) from exc
    technical_passed = (
        report.get("execution_status") == "complete"
        and report.get("automated_checks_passed") is True
        and report.get("case_count") == len(report["cases"])
        and report.get("expected_case_count") == len(report["cases"])
        and all(case.get("automated_check_passed") is True for case in report["cases"])
        and bool(report.get("corpus_generation_id"))
        and all(
            case.get("generation_id") == report["corpus_generation_id"]
            for case in report["cases"]
        )
    )
    real_run = report.get("synthetic") is False and all(
        case.get("answer_status") != "synthetic_demo" for case in report["cases"]
    )
    approved = not pending and not failed and has_judgments
    return {
        "evaluation_id": report["evaluation_id"],
        "report_sha256": report_digest(report),
        "technical_checks_passed": technical_passed,
        "review_status": (
            "needs_revision"
            if failed
            else "domain_review_required"
            if pending
            else "domain_review_approved"
        ),
        "reviewer": review.get("reviewer", ""),
        "reviewer_role": review.get("reviewer_role", ""),
        "reviewed_at": review.get("reviewed_at", ""),
        "pending_cases": pending,
        "failed_cases": failed,
        "synthetic": not real_run,
        "accepted": technical_passed and approved and real_run,
        "scope": (
            "Recorded legal-answer cases only; excludes property and release approval."
        ),
    }


def read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read JSON from {path}.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Report and review files must contain JSON objects.")
    return payload


@contextmanager
def private_json_output(path: Path | None):
    """Reserve an explicit output before paid work; never overwrite a report."""
    if path is None:
        yield lambda _payload: None
        return
    path = path.expanduser().resolve()
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        raise ValueError(
            "Choose a new report path in an existing writable folder."
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:

            def write(payload):
                json.dump(
                    payload, handle, ensure_ascii=False, indent=2, allow_nan=False
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())

            yield write
    except BaseException:
        path.unlink(missing_ok=True)
        raise
