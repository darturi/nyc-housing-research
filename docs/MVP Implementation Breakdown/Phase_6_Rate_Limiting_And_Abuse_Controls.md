# Phase 6: Rate Limiting and Abuse Controls Implementation

## Goal

Add MVP-grade abuse controls around authentication, search, and answer
generation. Phase 6 should prevent repeated login guessing, stop one user from
exhausting shared search or LLM capacity, enforce request-size and timeout
limits, and prepare the app for a future paid answer provider.

This phase should use PostgreSQL-backed state so the private MVP remains simple
to operate. It should not add Redis, public signup, billing, multi-tenant
quotas, streaming enforcement, or a full admin dashboard. Those can come later
if traffic or operational needs justify them.

## Scope

### Included

-   Database-backed rate limit events
-   Per-IP failed-login cooldowns
-   Per-user authenticated search request limits
-   Per-user authenticated answer request limits
-   Per-user daily LLM token budget checks
-   Request body size limits for API requests
-   Timeout limits for search and answer calls
-   `429 Too Many Requests` responses with clear retry metadata
-   Settings for all initial limits
-   Tests for limit decisions, endpoint enforcement, cooldowns, and budget
    exhaustion
-   README updates and work log entry after implementation

### Explicitly Deferred

-   Redis-backed distributed rate limiting
-   Public signup abuse controls
-   Billing or paid plan enforcement
-   Organization-level quotas
-   Admin UI for quota inspection
-   Streaming response budget enforcement
-   CAPTCHA
-   IP reputation or external fraud tooling
-   Per-source or per-model budget allocation

## Data Dependencies

Phase 6 assumes the previous phases have already created:

-   `users`
-   `sessions`
-   Authenticated `/search`
-   Authenticated `/answer`
-   `retrieval_logs`
-   `answer_logs` with token usage fields

The daily LLM budget should use `answer_logs.total_token_count` where available
and conservative configured estimates where token counts are unavailable.

## Deliverables

-   `rate_limit_events` table and Alembic migration
-   Optional user quota columns or a separate user quota table
-   Rate limit service module
-   Login attempt tracking and cooldown enforcement
-   Authenticated endpoint quota dependencies
-   Request body size middleware
-   Search and answer timeout enforcement
-   Standard rate limit error response helper
-   Environment settings for limits and windows
-   Tests and fixtures
-   README updates
-   Work log entry after implementation

## Design Principles

### Layered Controls

Use different controls for different risks:

-   IP-based checks for unauthenticated login attempts
-   User-based checks for authenticated search and answer requests
-   Token-budget checks for LLM-backed answer generation
-   Request size checks before parsing large bodies
-   Timeout checks around expensive retrieval and generation work

### Database First

Use PostgreSQL for MVP limit state. This keeps deployment simple and makes
limits auditable through ordinary SQL. If the app later runs multiple high
traffic instances, Redis can replace the storage backend behind the same rate
limit service.

### Fail Closed for Expensive Work

If the app cannot determine whether a user has exceeded answer or token limits,
it should refuse expensive answer generation instead of allowing unbounded LLM
usage. Health checks should remain unaffected.

### Keep Tests Deterministic

Time-window logic should accept an injectable clock or use small helper
functions that tests can exercise without sleeping. Default tests should not
call paid APIs.

### Minimize User Data

Store enough event data to enforce limits and audit abuse. Avoid recording raw
passwords, secrets, provider prompts, or unnecessary request bodies in rate
limit records.

## Initial Limits

Use the roadmap defaults:

-   Failed login attempts: 5 per IP per 15 minutes
-   Answer requests: 30 per user per hour
-   Search requests: 120 per user per hour
-   Daily LLM budget: configurable per user
-   Max question/query length: 4,000 characters
-   Max retrieved chunks sent to LLM: 8 to 12

Suggested default daily token budget:

-   `USER_DAILY_LLM_TOKEN_BUDGET=30000`

This is intentionally conservative for a private MVP and can be raised after
observing real usage.

## Suggested Files

``` text
app/
├── core/
│   └── middleware.py
├── limits/
│   ├── __init__.py
│   ├── dependencies.py
│   ├── errors.py
│   ├── schemas.py
│   ├── service.py
│   └── time.py
├── models/
│   ├── rate_limit_event.py
│   └── user_quota.py
└── db/migrations/versions/
    └── 20260705_0006_add_rate_limit_tables.py
tests/
├── test_rate_limit_auth.py
├── test_rate_limit_dependencies.py
├── test_rate_limit_service.py
├── test_request_size_middleware.py
└── test_answer_token_budget.py
```

If per-user quotas are added directly to `users`, skip `user_quota.py` and
document the columns in the migration.

## Database Changes

### `rate_limit_events`

Records events used to enforce rolling-window limits.

Columns:

-   `id`: UUID primary key
-   `scope`: `ip`, `user`, or `global`
-   `key`: IP address, user ID, or global key
-   `event_type`: `login_failed`, `search_request`, `answer_request`,
    `answer_provider_error`, or `request_rejected`
-   `endpoint`: nullable string such as `/auth/login`, `/search`, or `/answer`
-   `user_id`: nullable foreign key to `users.id`
-   `ip_address`: nullable string
-   `metadata`: JSON
-   `created_at`: timestamp

Indexes:

-   `(scope, key, event_type, created_at)`
-   `user_id`
-   `ip_address`
-   `created_at`

Notes:

-   Store failed login attempts even when the email address does not map to a
    user.
-   Do not store submitted passwords.
-   Consider hashing IP addresses later if privacy requirements increase.

### `user_quotas`

Optional but preferred for configurable per-user budgets.

Columns:

-   `id`: UUID primary key
-   `user_id`: foreign key to `users.id`, unique
-   `search_requests_per_hour`: nullable integer
-   `answer_requests_per_hour`: nullable integer
-   `daily_llm_token_budget`: nullable integer
-   `is_rate_limit_exempt`: boolean
-   `created_at`: timestamp
-   `updated_at`: timestamp

Behavior:

-   Null quota values should fall back to global settings.
-   `is_rate_limit_exempt` should be reserved for trusted admin/debug users and
    should not bypass failed-login protections.

Alternative:

-   Add the same quota fields to `users` if avoiding another table is more
    consistent with the current codebase.

## Environment Variables

Add to `.env.example`:

``` text
RATE_LIMIT_ENABLED=true
LOGIN_FAILED_LIMIT=5
LOGIN_FAILED_WINDOW_SECONDS=900
LOGIN_COOLDOWN_SECONDS=900
SEARCH_REQUESTS_PER_HOUR=120
ANSWER_REQUESTS_PER_HOUR=30
USER_DAILY_LLM_TOKEN_BUDGET=30000
MAX_REQUEST_BODY_BYTES=65536
SEARCH_TIMEOUT_SECONDS=10
ANSWER_TIMEOUT_SECONDS=30
RATE_LIMIT_EVENT_RETENTION_DAYS=30
```

Notes:

-   `MAX_REQUEST_BODY_BYTES=65536` is enough for current JSON request shapes.
-   Keep existing Pydantic field limits for `query` and `question`.
-   Request body limits should apply before JSON parsing.

## Rate Limit Service

The service should expose small primitives:

``` text
record_event(db, scope, key, event_type, ...)
count_events(db, scope, key, event_type, since)
check_window_limit(db, scope, key, event_type, limit, window_seconds)
check_daily_token_budget(db, user_id, requested_estimate)
```

Return a structured decision:

``` text
allowed: boolean
limit: integer
remaining: integer
reset_at: datetime
retry_after_seconds: integer
reason: string
```

Use this decision to set response headers when possible:

-   `Retry-After`
-   `X-RateLimit-Limit`
-   `X-RateLimit-Remaining`
-   `X-RateLimit-Reset`

## Login Abuse Flow

For `POST /auth/login`:

1.  Extract client IP from `request.client.host`.
2.  Check `login_failed` events for that IP within
    `LOGIN_FAILED_WINDOW_SECONDS`.
3.  If the limit is exceeded, return `429 Too Many Requests`.
4.  Attempt normal authentication.
5.  On invalid email, inactive user, or invalid password, record
    `login_failed`.
6.  On successful login, do not delete old events. Let the window expire
    naturally for auditability.

Do not distinguish unknown email from wrong password in the response.

## Authenticated Request Flow

For `POST /search`:

1.  Authenticate the user.
2.  Check `search_request` events for the user within the hourly window.
3.  If allowed, record `search_request`.
4.  Run the existing search endpoint.

For `POST /answer`:

1.  Authenticate the user.
2.  Check `answer_request` events for the user within the hourly window.
3.  Check the daily LLM token budget before provider invocation.
4.  If allowed, record `answer_request`.
5.  Run answer generation.
6.  Use `answer_logs.total_token_count` after completion for future budget
    checks.

Budget checks should use a conservative estimate before generation, for
example:

``` text
ANSWER_MAX_CONTEXT_CHARS / 4 + ANSWER_MAX_OUTPUT_TOKENS
```

This avoids starting an LLM call that is likely to exceed the daily limit.

## Request Body Size Middleware

Add middleware before route handling that:

-   Reads `Content-Length` when present
-   Rejects requests larger than `MAX_REQUEST_BODY_BYTES`
-   Returns `413 Payload Too Large`
-   Skips `/health`
-   Applies to JSON API endpoints

If `Content-Length` is absent, the first implementation may rely on server
defaults and schema validation. Avoid buffering very large bodies in
application memory for the MVP.

## Timeout Enforcement

Add timeout wrappers around expensive route work:

-   `/search`: `SEARCH_TIMEOUT_SECONDS`
-   `/answer`: `ANSWER_TIMEOUT_SECONDS`

For synchronous FastAPI handlers, prefer a service-level deadline check or move
expensive calls behind a helper that can be timed and fail with `504 Gateway
Timeout`. If converting handlers to async creates more churn than value, keep
Phase 6 scoped to bounded service timers and clear error handling.

Timeout response:

``` json
{
  "detail": "Request timed out."
}
```

Status:

``` text
504 Gateway Timeout
```

## Error Responses

Rate limit response:

``` json
{
  "detail": "Rate limit exceeded.",
  "reason": "answer_requests_per_hour",
  "retry_after_seconds": 600
}
```

Status:

``` text
429 Too Many Requests
```

Payload too large response:

``` json
{
  "detail": "Request body too large."
}
```

Status:

``` text
413 Payload Too Large
```

## Cleanup and Retention

Add a CLI command or service helper to delete old `rate_limit_events`:

``` text
uv run python -m app.cli.limits prune-events
```

Default retention:

-   `RATE_LIMIT_EVENT_RETENTION_DAYS=30`

This can be a manual command in Phase 6. Scheduled cleanup can wait for Phase 7
deployment operations.

## Testing Plan

### Unit Tests

-   Window counting includes events inside the window and excludes older events.
-   Limit decisions return remaining count, reset time, and retry delay.
-   Token budget checks include previous `answer_logs.total_token_count`.
-   Null user quota values fall back to global settings.
-   Exempt users bypass authenticated search/answer request limits but not login
    protections.

### Auth Tests

-   Five failed logins from one IP are allowed.
-   The sixth failed login from the same IP returns `429`.
-   A different IP is not blocked by the first IP's failures.
-   Successful login still works before the limit is reached.
-   Responses do not reveal whether the email exists.

### Search and Answer Tests

-   Search requests over the hourly limit return `429`.
-   Answer requests over the hourly limit return `429`.
-   Daily token budget exhaustion blocks `/answer`.
-   Rejected answer requests do not call the answer provider.
-   Existing citation validation and unsupported-answer behavior still pass.

### Middleware Tests

-   Requests larger than `MAX_REQUEST_BODY_BYTES` return `413`.
-   `/health` is not blocked by body-size middleware.
-   Normal request bodies still parse successfully.

### Timeout Tests

-   Search timeout returns `504`.
-   Answer timeout returns `504`.
-   Timeout cases are logged as rejected or failed events where useful.

## Acceptance Criteria

-   Excessive login attempts return `429 Too Many Requests`.
-   Excessive authenticated search requests return `429 Too Many Requests`.
-   Excessive authenticated answer requests return `429 Too Many Requests`.
-   Daily LLM token budget exhaustion blocks answer generation before provider
    invocation.
-   Oversized request bodies return `413 Payload Too Large`.
-   Search and answer timeout paths return `504 Gateway Timeout`.
-   Rate limit responses include retry metadata.
-   Default tests pass without paid API calls.
-   Limits are configurable through environment variables.

## Implementation Order

1.  Add settings and `.env.example` entries.
2.  Add `rate_limit_events` and quota schema migration.
3.  Add rate limit models to `app.models`.
4.  Add rate limit service and decision schemas.
5.  Add standard `429` error helper and headers.
6.  Add login failed-attempt tracking to `/auth/login`.
7.  Add authenticated quota dependencies to `/search` and `/answer`.
8.  Add daily token budget check before answer provider invocation.
9.  Add request body size middleware.
10. Add search and answer timeout handling.
11. Add pruning helper or CLI command for old rate limit events.
12. Add tests.
13. Update README.
14. Add Phase 6 work log after implementation.

## Manual Verification

After implementation, run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
```

Then manually test:

-   Send repeated bad login attempts until `429`.
-   Send repeated authenticated `/search` requests until `429`.
-   Send repeated authenticated `/answer` requests until `429`.
-   Lower `USER_DAILY_LLM_TOKEN_BUDGET` and confirm `/answer` is blocked before
    provider invocation.
-   Send an oversized JSON request and confirm `413`.
-   Confirm `/health` remains available.

## Next Step

After Phase 6 is complete, Phase 7 should focus on deployment and operations:
HTTPS-only production access, secret management, backups, object storage
lifecycle policy, uptime/error monitoring, ingestion runbooks, and rollback
instructions.
