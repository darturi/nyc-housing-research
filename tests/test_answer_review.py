import copy
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.answer.evaluation import answer_evaluation_payload, run_answer_evaluation
from app.answer.local import LocalAnswerEvidence
from app.answer.review import (
    REVIEW_DIMENSIONS,
    assess_review,
    create_review_template,
    private_json_output,
)
from app.cli.main import EXIT_VALIDATION_FAILED, main
from app.corpus.service import CorpusService, SourceArtifact
from tests.test_local_retrieval_evaluation import evaluation_corpus as evaluation_corpus


def _evidence(marker, citation):
    return LocalAnswerEvidence(
        marker=marker,
        citation=citation,
        chunk_id=f"chunk-{marker}",
        source_slug="ny-rpapl",
        title="Fixture provision",
        source_name="Fixture law",
        source_url="https://example.org/fixture",
        publisher="Fixture publisher",
        origin="core",
        category="law",
        source_version_id="fixture-version",
        content_hash="fixture-hash",
        locator={},
        retrieved_at="2026-09-20",
        last_checked_at="2026-09-20",
        effective_from=None,
        effective_to=None,
        excerpt="Authored fixture evidence only.",
    )


@pytest.mark.parametrize(
    "answer,passed",
    [
        ("Fixture statement [E1].", False),
        ("Fixture statement [E2].", True),
        ("Fixture statement [E2] [E99].", False),
        ("Fixture statement without a citation.", False),
    ],
)
def test_answer_gate_checks_cited_evidence_not_just_retrieved_evidence(
    evaluation_corpus, monkeypatch, answer, passed
):
    context, storage = evaluation_corpus
    case = {
        "id": "fixture-1",
        "route": "legal",
        "question": "RPAPL 711?",
        "expected_behavior": "general_answer",
        "required_propositions": ["fixture rule"],
        "relevant_citations": ["RPAPL § 711"],
    }
    monkeypatch.setattr(
        "app.answer.evaluation.load_legal_review_cases",
        lambda _: {
            "review_status": "domain_review_required",
            "cases": [case],
        },
    )
    generation = CorpusService(storage).status().active_generation_id
    monkeypatch.setattr(
        "app.answer.evaluation.LocalAnswerService.answer",
        lambda *a, **k: SimpleNamespace(
            answer=answer,
            status="answered",
            error=None,
            evidence=(_evidence("E1", "RPAPL § 713"), _evidence("E2", "RPAPL § 711")),
            generation_id=generation,
            prompt_version="fixture-prompt",
        ),
    )
    report = run_answer_evaluation(
        context, storage, approve_cost=False, max_cost_usd=None
    )
    payload = answer_evaluation_payload(report)
    assert report.automated_checks_passed is passed
    assert report.review_status == "domain_review_required"
    assert report.cases[0].returned_citations == ("RPAPL § 713", "RPAPL § 711")
    assert payload["cases"][0]["evidence"][1] == asdict(_evidence("E2", "RPAPL § 711"))
    assert payload["corpus_generation_id"] == generation
    assert payload["automated_check_scope"] == "status_and_cited_evidence_only"
    assert payload["profile_snapshots"]["answer"]["input_usd_per_million"] == "0"


def _report():
    # Review-aggregation fixture, not evidence of an actual model/domain review.
    return {
        "format": "nyc-housing-answer-evaluation",
        "format_version": 1,
        "evaluation_id": "fixture-evaluation",
        "execution_status": "complete",
        "automated_checks_passed": True,
        "synthetic": False,
        "case_count": 1,
        "expected_case_count": 1,
        "corpus_generation_id": "frozen",
        "cases": [
            {
                "case_id": "case-1",
                "answer": "Fixture statement [E1].",
                "automated_check_passed": True,
                "answer_status": "answered",
                "generation_id": "frozen",
            }
        ],
    }


def test_answer_evaluation_stops_before_another_call_if_corpus_changes(
    evaluation_corpus, monkeypatch
):
    context, storage = evaluation_corpus
    service = CorpusService(storage)
    original = service.status().active_generation_id
    cases = [
        {
            "id": str(index),
            "route": "legal",
            "question": "RPAPL 711?",
            "expected_behavior": "general_answer",
            "required_propositions": [],
            "relevant_citations": ["RPAPL § 711"],
        }
        for index in range(2)
    ]
    monkeypatch.setattr(
        "app.answer.evaluation.load_legal_review_cases",
        lambda _: {
            "review_status": "domain_review_required",
            "cases": cases,
        },
    )
    calls = []

    def answer(*args, **kwargs):
        calls.append(True)
        service.install_artifacts(
            [
                SourceArtifact(
                    slug="ny-rpapl",
                    content=b"\xc2\xa7 711. Replacement fixture.",
                    source_url=service.manifests["ny-rpapl"].source_url,
                    content_type="text/plain",
                    retrieved_at=datetime.now(UTC),
                )
            ],
            allow_partial=True,
        )
        return SimpleNamespace(
            answer="Fixture [E1].",
            status="answered",
            error=None,
            evidence=(_evidence("E1", "RPAPL § 711"),),
            generation_id=original,
            prompt_version="fixture",
        )

    monkeypatch.setattr("app.answer.evaluation.LocalAnswerService.answer", answer)
    result = run_answer_evaluation(
        context, storage, approve_cost=False, max_cost_usd=None
    )
    assert len(calls) == 1
    assert result.execution_status == "stopped_corpus_changed"
    assert not result.automated_checks_passed
    assert result.case_count == 1
    assert result.expected_case_count == 2


def _completed_review(report):
    review = create_review_template(report)
    review.update(
        reviewer="Fixture reviewer",
        reviewer_role="Fixture role",
        reviewed_at="2026-09-20T12:00:00+00:00",
    )
    for case in review["cases"]:
        case.update({dimension: "pass" for dimension in REVIEW_DIMENSIONS})
    return review


def test_review_defaults_to_pending_and_requires_each_dimension():
    report = _report()
    pending = assess_review(report, create_review_template(report))
    assert pending["accepted"] is False
    assert pending["review_status"] == "domain_review_required"
    review = _completed_review(report)
    assert assess_review(report, review)["accepted"] is True
    review["cases"][0]["accuracy"] = "pending"
    assert assess_review(report, review)["accepted"] is False
    review["cases"][0].update(accuracy="fail", notes="Fixture incorrect rule")
    assert assess_review(report, review)["review_status"] == "needs_revision"


@pytest.mark.parametrize(
    "change",
    [
        {"synthetic": True},
        {"execution_status": "stopped_provider_error"},
        {"automated_checks_passed": False},
        {"expected_case_count": 2},
    ],
)
def test_human_pass_does_not_override_synthetic_incomplete_or_failed_run(change):
    report = _report() | change
    assert assess_review(report, _completed_review(report))["accepted"] is False


def test_review_rejects_changed_answers_wrong_case_sets_and_missing_reviewer():
    report = _report()
    review = _completed_review(report)
    changed = copy.deepcopy(report)
    changed["cases"][0]["answer"] = "Different answer"
    with pytest.raises(ValueError, match="exact evaluation report"):
        assess_review(changed, review)
    review["cases"].append(copy.deepcopy(review["cases"][0]))
    with pytest.raises(ValueError, match="exactly once"):
        assess_review(report, review)
    review = _completed_review(report)
    review["reviewer"] = ""
    with pytest.raises(ValueError, match="reviewer"):
        assess_review(report, review)


def test_review_cli_round_trip_does_not_need_a_workspace(tmp_path, capsys):
    report_path, review_path = tmp_path / "report.json", tmp_path / "review.json"
    report = _report()
    report_path.write_text(json.dumps(report))
    args = ["--data-dir", str(tmp_path / "absent"), "review-answers", str(report_path)]
    assert main([*args, "--template", str(review_path), "--json"]) == 0
    capsys.readouterr()
    if os.name != "nt":
        assert review_path.stat().st_mode & 0o777 == 0o600
    assert (
        main([*args, "--review", str(review_path), "--json"]) == EXIT_VALIDATION_FAILED
    )
    assert json.loads(capsys.readouterr().out)["accepted"] is False
    review_path.write_text(json.dumps(_completed_review(report)))
    assert main([*args, "--review", str(review_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["accepted"] is True
    assert not (tmp_path / "absent").exists()


def test_report_path_is_reserved_before_paid_work_and_never_overwritten(
    evaluation_corpus, tmp_path, monkeypatch
):
    context, _storage = evaluation_corpus
    target = tmp_path / "existing.json"
    target.write_text("keep original")

    def unexpected_provider(*args, **kwargs):
        pytest.fail("evaluation must not start if output cannot be reserved")

    monkeypatch.setattr("app.cli.main.run_answer_evaluation", unexpected_provider)
    assert (
        main(
            [
                "--data-dir",
                str(context.paths.root),
                "evaluate",
                "--answers",
                "--report",
                str(target),
                "--json",
            ]
        )
        != 0
    )
    assert target.read_text() == "keep original"
    new_target = tmp_path / "interrupted.json"
    with pytest.raises(RuntimeError):
        with private_json_output(new_target):
            raise RuntimeError("interrupted")
    assert not new_target.exists()


def test_fake_answer_run_saves_an_explicit_reviewable_report(
    evaluation_corpus, tmp_path, capsys
):
    context, _storage = evaluation_corpus
    target = tmp_path / "answers.json"
    result = main(
        [
            "--data-dir",
            str(context.paths.root),
            "evaluate",
            "--answers",
            "--report",
            str(target),
            "--json",
        ]
    )
    assert result in (0, EXIT_VALIDATION_FAILED)
    report = json.loads(target.read_text())
    assert report == json.loads(capsys.readouterr().out)
    assert report["case_count"] == report["expected_case_count"] == 26
    assert report["synthetic"] is True
    assert report["review_status"] == "domain_review_required"
    assert any(case["evidence"] for case in report["cases"])
    assert assess_review(report, _completed_review(report))["accepted"] is False
