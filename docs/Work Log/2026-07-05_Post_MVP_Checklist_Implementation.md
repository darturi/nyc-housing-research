# Work Log: Post-MVP Checklist Implementation

Date: 2026-07-05

## Summary

Implemented the repo-side checklist items identified after Phase 7, with the UI
explicitly deferred.

This work added OpenAI-compatible embedding and answer providers, S3-compatible
artifact storage support, HPD lookup service reuse, deterministic query
routing, production provider hardening, ingestion reliability improvements,
operational CLI commands, evaluation fixtures, smoke-test CLI support, tests,
and documentation updates.

## Files Added

-   `app/api/query.py`
-   `app/cli/evaluate.py`
-   `app/cli/ops.py`
-   `app/cli/smoke.py`
-   `app/hpd/__init__.py`
-   `app/hpd/search.py`
-   `app/query_routing/__init__.py`
-   `app/query_routing/router.py`
-   `app/schemas/query.py`
-   `tests/fixtures/evaluation_questions.json`
-   `tests/test_artifact_s3.py`
-   `tests/test_config_hardening.py`
-   `tests/test_query_routing.py`
-   `tests/test_real_providers.py`

## Files Updated

-   `.env.example`
-   `README.md`
-   `app/answer/providers.py`
-   `app/cli/ingest.py`
-   `app/core/config.py`
-   `app/ingestion/artifacts.py`
-   `app/ingestion/downloaders.py`
-   `app/main.py`
-   `app/retrieval/embeddings.py`
-   `app/api/hpd.py`
-   `app/schemas/hpd.py`
-   `docs/Runbooks/Deployment.md`
-   `docs/Runbooks/Ingestion.md`
-   `docs/Runbooks/Monitoring.md`

## Implementation Details

### Real Provider Support

Added OpenAI-compatible embedding and answer providers while preserving the
fake providers for tests and local development.

Embedding provider support includes:

-   API key and base URL settings
-   Batch requests
-   Timeout and retry settings
-   Response count validation
-   Embedding dimension validation

Answer provider support includes:

-   API key and base URL settings
-   Chat-completion style request support
-   Structured JSON output parsing
-   Token usage capture
-   Timeout and retry settings
-   Existing backend citation validation remains authoritative

### Production Hardening

Production config now rejects fake providers unless
`ALLOW_FAKE_PROVIDERS_IN_PRODUCTION=true` is explicitly set. Production already
rejects insecure session cookies, disabled rate limiting, and SQLite.

### Artifact Storage

Added S3-compatible artifact storage behind the existing artifact functions:

-   `write_artifact`
-   `read_artifact`
-   `artifact_exists`

The S3 backend uses optional `boto3`; local storage remains the default for
tests and development.

### HPD Lookup and Query Routing

Moved HPD lookup logic into `app/hpd/search.py` and kept the existing
authenticated `/hpd/violations/search` endpoint.

Added authenticated `/query` routing:

-   Property/HPD questions with an address, building ID, or registration ID
    route to HPD violation lookup.
-   Legal questions route to answer generation.
-   Routing is deterministic and does not call an LLM.

### Ingestion Reliability

Added HTTP retry settings for source downloads.

Added ingestion commands:

``` text
uv run python -m app.cli.ingest ingest-missing
uv run python -m app.cli.ingest verify-traceability
```

`ingest-missing` continues across sources and reports all failures at the end.

### Operational Commands

Added:

``` text
uv run python -m app.cli.smoke
uv run python -m app.cli.evaluate --user-email admin@example.com
uv run python -m app.cli.ops log-summary
uv run python -m app.cli.ops rate-limits
uv run python -m app.cli.ops source-freshness
```

## Verification

Commands run:

``` text
uv run --extra dev ruff check .
uv run --extra dev pytest
uv run python -m app.cli.ingest ingest-missing
uv run python -m app.cli.ingest status
uv run python -m app.cli.ingest verify-traceability
uv run python -m app.cli.embeddings status
uv run python -m app.cli.ops log-summary
uv run python -m app.cli.ops rate-limits
uv run python -m app.cli.ops source-freshness
```

Results:

-   `ruff check`: passed.
-   `pytest`: passed, 77 tests.
-   `ingest-missing`: loaded HPD violations but reported blocked/moved legal
    and guidance sources.
-   `ingest status`: `sources: 5`, `source_versions: 1`, `documents: 0`,
    `chunks: 0`, `hpd_violations: 5000`, `ingestion_runs: 9`.
-   `verify-traceability`: `missing_artifacts: 0`.
-   `embeddings status`: `embedded: 0`, `missing: 0` because no legal chunks
    are currently ingested.

## Known Notes

-   The NYC Administrative Code source returned `403 Forbidden` from the
    current non-browser ingestion client.
-   The NY Senate legal source pages returned `403 Forbidden` from the current
    non-browser ingestion client.
-   The HPD guidance landing page URL returned `404 Not Found`.
-   HPD violations ingestion succeeded and loaded 5,000 records.
-   The S3 backend requires `boto3` to be installed in the runtime environment
    when `ARTIFACT_STORAGE_BACKEND=s3`.

## Next Step

Resolve permitted machine-readable legal/guidance source access. Options
include official APIs with keys where available, downloadable bulk files, or
manual source exports that preserve public URL and source-version traceability.
