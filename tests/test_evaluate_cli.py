import json

from app.answer.schemas import AnswerCitation, AnswerResult
from app.cli import evaluate
from tests.retrieval_fixtures import create_test_user


def test_evaluate_expectations_accepts_answered_result():
    answer = answer_result(
        answer_status="answered",
        answer="Owners must keep the premises in good repair.",
        citations=[
            AnswerCitation(
                chunk_id="chunk-1",
                citation="NYC Admin Code § 27-2005",
                source_name="NYC Housing Maintenance Code",
                source_url="https://example.com/hmc",
            )
        ],
    )

    failures = evaluate.evaluate_expectations(
        {
            "expected_status": "answered",
            "min_citation_count": 1,
            "required_source_type": "law",
            "required_source_name_contains": "Housing Maintenance Code",
            "required_citation_contains": "27-2005",
            "required_answer_terms": ["good repair"],
        },
        answer,
        {"chunk-1": "law"},
    )

    assert failures == []


def test_evaluate_expectations_accepts_unsupported_result():
    answer = answer_result(answer_status="unsupported", answer="Not enough corpus.")

    failures = evaluate.evaluate_expectations(
        {"expected_status": "unsupported", "min_citation_count": 0},
        answer,
    )

    assert failures == []


def test_evaluate_expectations_reports_actionable_failures():
    answer = answer_result(answer_status="unsupported", answer="Not enough corpus.")

    failures = evaluate.evaluate_expectations(
        {
            "expected_status": "answered",
            "min_citation_count": 1,
            "required_source_type": "guidance",
            "required_source_name_contains": "HPD",
            "required_citation_contains": "27-2029",
            "required_answer_terms": ["complaint"],
        },
        answer,
    )

    assert "expected answer_status 'answered', got 'unsupported'" in failures
    assert "expected at least 1 citations, got 0" in failures
    assert "expected a citation from source_type 'guidance'" in failures
    assert "expected a citation source name containing 'HPD'" in failures
    assert "expected a citation containing '27-2029'" in failures
    assert "expected answer to contain 'complaint'" in failures


def test_evaluate_questions_returns_zero_when_expectations_pass(
    monkeypatch,
    tmp_path,
    capsys,
):
    create_test_user()
    question_file = write_question_file(
        tmp_path,
        [
            {
                "id": "passing-question",
                "question": "What does the code require?",
                "expected_status": "answered",
                "min_citation_count": 1,
                "required_source_type": "law",
            }
        ],
    )
    monkeypatch.setattr(evaluate, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(
        evaluate,
        "source_types_for_citations",
        lambda _db, _citations: {"chunk-1": "law"},
    )

    exit_code = evaluate.evaluate_questions(
        str(question_file),
        "admin@example.com",
        5,
    )

    captured = capsys.readouterr()
    row = json.loads(captured.out)
    assert exit_code == 0
    assert row["passed"] is True
    assert row["failures"] == []
    assert captured.err == ""


def test_evaluate_questions_returns_one_when_expectations_fail(
    monkeypatch,
    tmp_path,
    capsys,
):
    create_test_user()
    question_file = write_question_file(
        tmp_path,
        [
            {
                "id": "failing-question",
                "question": "What does the code require?",
                "expected_status": "answered",
                "min_citation_count": 2,
            }
        ],
    )
    monkeypatch.setattr(evaluate, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(
        evaluate,
        "source_types_for_citations",
        lambda _db, _citations: {"chunk-1": "law"},
    )

    exit_code = evaluate.evaluate_questions(
        str(question_file),
        "admin@example.com",
        5,
    )

    captured = capsys.readouterr()
    row = json.loads(captured.out)
    assert exit_code == 1
    assert row["passed"] is False
    assert "expected at least 2 citations, got 1" in row["failures"]
    assert "Evaluation failed." in captured.err
    assert "failing-question" in captured.err


def test_evaluate_questions_returns_one_for_missing_user(tmp_path, capsys):
    question_file = write_question_file(
        tmp_path,
        [{"id": "any-question", "question": "Anything?"}],
    )

    exit_code = evaluate.evaluate_questions(
        str(question_file),
        "missing@example.com",
        5,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "User not found: missing@example.com" in captured.err


def fake_generate_answer(_db, _user, question, _filters, _limit):
    return answer_result(
        answer_status="answered",
        answer=f"Answer for {question}: keep premises in good repair.",
        citations=[
            AnswerCitation(
                chunk_id="chunk-1",
                citation="NYC Admin Code § 27-2005",
                source_name="NYC Housing Maintenance Code",
                source_url="https://example.com/hmc",
            )
        ],
    )


def answer_result(
    *,
    answer_status: str,
    answer: str,
    citations: list[AnswerCitation] | None = None,
) -> AnswerResult:
    return AnswerResult(
        question="Question?",
        answer_status=answer_status,
        answer=answer,
        citations=citations or [],
        source_coverage=None,
        disclaimer="Not legal advice.",
    )


def write_question_file(tmp_path, questions: list[dict]):
    question_file = tmp_path / "questions.json"
    question_file.write_text(json.dumps(questions))
    return question_file
