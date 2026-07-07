# Work Log: Phase 6 Rate Limiting and Abuse Controls

Date: 2026-07-05

## Summary

Implemented Phase 6 of the NYC Housing Law RAG MVP based on
`docs/MVP Implementation Breakdown/Phase_6_Rate_Limiting_And_Abuse_Controls.md`.

This phase added PostgreSQL-backed abuse controls around login, search, and
answer generation: failed-login throttling, per-user search and answer request
limits, daily answer token budget checks, request body size enforcement,
timeout responses, rate limit event retention pruning, tests, and README
documentation.

## Files Added

-   `app/cli/limits.py`
-   `app/core/middleware.py`
-   `app/limits/__init__.py`
-   `app/limits/dependencies.py`
-   `app/limits/errors.py`
-   `app/limits/schemas.py`
-   `app/limits/service.py`
-   `app/limits/time.py`
-   `app/models/rate_limit_event.py`
-   `app/models/user_quota.py`
-   `app/db/migrations/versions/20260705_0006_add_rate_limit_tables.py`
-   `tests/test_answer_token_budget.py`
-   `tests/test_rate_limit_auth.py`
-   `tests/test_rate_limit_dependencies.py`
-   `tests/test_rate_limit_service.py`
-   `tests/test_request_size_middleware.py`

## Files Updated

-   `.env.example`
-   `README.md`
-   `app/api/answers.py`
-   `app/api/auth.py`
-   `app/api/search.py`
-   `app/core/config.py`
-   `app/main.py`
-   `app/models/__init__.py`

## Implementation Details

### Rate Limit Tables

Added:

-   `rate_limit_events`
-   `user_quotas`

`rate_limit_events` stores auditable rolling-window events for IP and user
limits. `user_quotas` allows per-user overrides for search requests, answer
requests, daily LLM token budgets, and trusted rate-limit exemptions.

### Login Protection

`POST /auth/login` now checks failed login attempts per IP before password
verification. Failed logins for unknown users, inactive users, and wrong
passwords all record the same `login_failed` event and keep the same generic
authentication error.

### Search and Answer Limits

Authenticated `/search` and `/answer` calls now record user-scoped request
events. Requests over the configured hourly limit return `429 Too Many
Requests` with retry metadata.

`/answer` also checks the user's daily answer token budget before invoking
answer generation. The preflight estimate uses configured context and output
limits so expensive provider calls can be blocked before they start.

### Request Size and Timeout Controls

Added request body size middleware based on `Content-Length`. Oversized API
requests return `413 Payload Too Large`; `/health` is excluded.

Search and answer routes now return `504 Gateway Timeout` if route-level
elapsed time exceeds the configured threshold.

### Maintenance

Added a pruning command:

``` text
uv run python -m app.cli.limits prune-events
```

It deletes `rate_limit_events` older than
`RATE_LIMIT_EVENT_RETENTION_DAYS`.

## Verification

Commands run:

``` text
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run alembic heads
uv run alembic current
uv run alembic upgrade head
uv run alembic current
```

Results:

-   `ruff check`: passed.
-   `pytest`: passed, 60 tests.
-   `alembic heads`: found `20260705_0006 (head)`.
-   `alembic current`: initially confirmed the local database was at
    `20260705_0005`.
-   `alembic upgrade head`: applied
    `20260705_0005 -> 20260705_0006`.

## Known Notes

-   Rate limit state is PostgreSQL-backed for the MVP. Redis remains deferred.
-   Timeout enforcement is route-level elapsed-time handling; it does not
    forcibly cancel already-running synchronous work.
-   The test suite still emits existing Starlette deprecation warnings from
    the dependency stack.

## Next Step

Phase 7 should focus on deployment and operations: HTTPS-only production
access, secret management, backups, object storage lifecycle policies,
monitoring, ingestion runbooks, and rollback instructions.
