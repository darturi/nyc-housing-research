# Answer Quality Implementation Plan

Status: **historical hosted-system plan, superseded for execution** by
`Local_Distribution_Implementation_Plan.md`. Commands and environment assumptions
below describe the prior PostgreSQL/user-based application and must not be used as
the local release-candidate runbook. Use `Answer_Quality_User_Runbook.md` for the
implemented local evaluator/debug workflow.

This plan covers the engineering work needed to improve answer quality while
the app is still usable with fake local providers.

## Goal

Make local answers coherent, testable, and diagnosable before switching the MVP
to a paid or production-like answer provider.

## Current Diagnosis

The weak heat-answer output is not primarily a retrieval failure. The retrieved
HMC chunk contains the relevant rule for NYC Admin Code section 27-2029, but the
configured answer provider is currently the deterministic fake provider:

```text
ANSWER_LLM_PROVIDER=fake
ANSWER_LLM_MODEL=fake-answer-small
```

The fake provider is useful for local development and tests, but its current
summary extraction is too brittle for legal text. It fails on section headings,
lettered subdivisions such as `a.`, and parenthesized clauses such as `(1)` and
`(2)`.

## Step 1: Improve The Deterministic Fallback Answer

Update the fake answer provider so it produces a coherent source-backed summary
from retrieved chunks.

Implementation targets:

- Strip legal section headings correctly, including `§ 27-2029` and repeated
  title text.
- Recognize lettered subdivisions such as `a.` and `b.`.
- Recognize parenthesized clauses such as `(1)` and `(2)`.
- Preserve important numeric and temporal details such as dates, times, and
  temperatures.
- Avoid malformed sentence joins.
- Keep citation behavior unchanged: cite only retrieved chunk IDs.

Likely files:

- `app/answer/providers.py`
- `tests/test_answer_service.py`
- `tests/test_answer_api.py`

Acceptance criteria:

- The heat question answer mentions the October 1 to May 31 heat season.
- The answer mentions 68 degrees F during the day when outside temperature is
  below 55 degrees F.
- The answer mentions 62 degrees F overnight.
- The answer still cites `NYC Admin Code § 27-2029`.
- Unsupported questions still return `answer_status="unsupported"`.

## Step 2: Add Answer Quality Evaluation Checks

The current evaluation mostly checks status and citation presence. That allows
thin or malformed answers to pass. Extend evaluation fixtures so supported
questions require key answer terms.

Implementation targets:

- Add required answer terms for HMC heat and good-repair questions.
- Add required answer terms for HPD guidance questions.
- Keep unsupported-case behavior strict.
- Make failures readable so weak answers are easy to diagnose.

Likely files:

- `tests/fixtures/evaluation_questions.json`
- `app/cli/evaluate.py` if richer checks are needed
- `tests/test_evaluate_cli.py`

Acceptance criteria:

- `uv run python -m app.cli.evaluate --user-email admin@example.com` fails if
  an answer has a citation but omits required rule details.
- The current HMC heat question requires the specific heat-season and
  temperature details.
- All evaluation failures identify the question ID and missing expectation.

## Step 3: Add A Retrieval And Answer Debug Command

Add a CLI command that explains one answer end to end. This should make future
answer failures diagnosable without manually querying the database.

Proposed command:

```text
uv run python -m app.cli.debug answer \
  --user-email admin@example.com \
  --question "What does the HMC say about heat and hot water?" \
  --limit 5
```

Output should include:

- Provider and model.
- Answer status.
- Generated answer text.
- Cited chunk IDs.
- Top retrieved chunks with rank, score, match type, citation, title, source,
  and text excerpt.
- Any missing citation or unsupported-answer reason.

Likely files:

- `app/cli/debug.py`
- `tests/test_debug_cli.py`

Acceptance criteria:

- The debug command works with the fake provider and local Postgres.
- The command exposes the retrieved text that the provider saw.
- The command can be run after a bad UI answer to determine whether retrieval
  or answer synthesis caused the problem.

## Verification Commands

Run the focused checks first:

```text
uv run --extra dev pytest tests/test_answer_service.py tests/test_evaluate_cli.py
uv run --extra dev ruff check app/answer app/cli tests
```

Then run the full suite:

```text
uv run --extra dev pytest
uv run --extra dev ruff check .
```

Finally verify against the local database:

```text
uv run python -m app.cli.evaluate --user-email admin@example.com
uv run python -m app.cli.debug answer \
  --user-email admin@example.com \
  --question "What does the HMC say about heat and hot water?"
```

## Out Of Scope

This implementation plan does not switch the app to a paid answer provider,
choose a model, add billing, or certify legal adequacy. Those decisions belong
to the MVP owner and domain reviewer.
