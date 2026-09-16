# Monitoring and Error Logging Runbook

> Historical hosted-mode runbook. Local installations use `nyc-housing status`
> and `nyc-housing doctor`; see [Troubleshooting](../Troubleshooting.md).

## Purpose

Define the minimum monitoring and logging needed to operate the private MVP.

## Uptime Monitoring

Configure an uptime check for:

``` text
GET /health
```

Alert on:

-   Repeated health check failures.
-   App crash or restart loops.
-   Database connection failures.
-   High `5xx` response rate.
-   Backup failures.
-   Answer provider failures if a paid provider is enabled.

## Operational Metrics to Review

Useful database-backed checks:

-   `retrieval_logs` count by day.
-   `answer_logs.answer_status` distribution.
-   `rate_limit_events` count by event type.
-   Latest successful ingestion run per source.
-   Embedding coverage from `app.cli.embeddings status`.

## Error Logging Rules

Production logs should capture:

-   App startup and shutdown.
-   Uncaught exceptions.
-   Provider failures.
-   Migration or database connection errors.
-   Failed ingestion runs.

Production logs must not capture:

-   Passwords.
-   Session tokens.
-   Cookies.
-   API keys.
-   Raw `.env` output.
-   Full provider prompts.
-   Raw request bodies.

If user questions may contain sensitive housing facts, avoid sending full
question text to hosted error tools. Prefer hashes, IDs, counts, statuses, and
structured metadata.

## Hosted Error Tool Scrubbing

If adding a hosted error logging DSN, configure it as a server-side secret and
scrub:

-   `Authorization`
-   `Cookie`
-   `nyc_housing_session`
-   `DATABASE_URL`
-   `EMBEDDING_API_KEY`
-   `ANSWER_LLM_API_KEY`
-   Request bodies

## Manual Checks

Run:

``` text
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
uv run python -m app.cli.ingest verify-traceability
uv run alembic current
```

Review recent `answer_logs`:

``` sql
SELECT answer_status, count(*)
FROM answer_logs
WHERE created_at >= now() - interval '1 day'
GROUP BY answer_status;
```

Review recent rate limit events:

``` sql
SELECT event_type, count(*)
FROM rate_limit_events
WHERE created_at >= now() - interval '1 day'
GROUP BY event_type;
```
