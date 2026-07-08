# Ingestion and Maintenance Runbook

## Purpose

Run public-source ingestion, regenerate embeddings, check corpus status, and
prune rate limit events.

## Source Rules

The MVP may ingest only freely accessible public sources. Do not ingest paid
legal databases, proprietary summaries, commercial headnotes, paid citators,
paywalled source material, or any source whose terms do not permit the intended
use.

Before ingestion:

-   Confirm a recent database backup exists.
-   Confirm source registry notes still show public access.
-   Check source acquisition status.
-   Confirm artifact storage has enough capacity.
-   Confirm production rate limits are enabled.

Check source acquisition status:

``` text
uv run python -m app.cli.ingest source-availability
```

Official source strategy:

-   NYC Admin Code / Housing Maintenance Code: official AmLegal bulk XML ZIP
    linked from the code library overview. The HMC importer stores the full ZIP
    and parses `XML/0-0-0-60027.xml`.
-   NY state statutes: official NY Senate PDF/API sources.
-   HPD and property datasets: NYC Open Data / Socrata APIs.

## Full MVP Ingestion

Seed source records:

``` text
uv run python -m app.cli.ingest seed-sources
```

Run all MVP ingestion:

``` text
uv run python -m app.cli.ingest ingest-mvp
```

Ingest only missing sources:

``` text
uv run python -m app.cli.ingest ingest-missing
```

If a configured public source blocks CLI download or moves, `ingest-missing`
prints a warning and continues with sources that can load. Do not substitute
unofficial mirrors.

Check ingestion status:

``` text
uv run python -m app.cli.ingest status
```

Verify artifact traceability:

``` text
uv run python -m app.cli.ingest verify-traceability
```

Generate embeddings:

``` text
uv run python -m app.cli.embeddings generate
```

Check embedding coverage:

``` text
uv run python -m app.cli.embeddings status
```

## Single Source Ingestion

Download and parse one legal or guidance source:

``` text
uv run python -m app.cli.ingest ingest-source nyc-housing-maintenance-code
```

Ingest a manually obtained official artifact when direct CLI download is
blocked. This is a fallback only; normal HMC ingestion uses the AmLegal bulk
XML ZIP:

``` text
uv run python -m app.cli.ingest ingest-artifact nyc-housing-maintenance-code \
  --file /path/to/XML.zip \
  --source-url https://codelibrary.amlegal.com/codes/newyorkcity/latest/NYCadmin/0-0-0-60027 \
  --content-type application/zip
```

Generate embeddings for one source:

``` text
uv run python -m app.cli.embeddings generate --source-slug nyc-housing-maintenance-code
```

## HPD Violations

Load HPD violations:

``` text
uv run python -m app.cli.ingest load-hpd-violations
```

## Post-Ingestion Smoke Tests

-   Authenticated `/search` returns source-backed chunks.
-   Authenticated `/answer` returns citations and disclaimer.
-   Exact citation queries prefer exact matches.
-   Source URLs in responses are public URLs.

Run the API-level MVP smoke checks after login credentials exist:

``` text
uv run python -m app.cli.smoke --email admin@example.com --password '<password>' --mvp-eval
```

## Rate Limit Event Maintenance

Prune old rate limit events:

``` text
uv run python -m app.cli.limits prune-events
```

Default retention is controlled by:

``` text
RATE_LIMIT_EVENT_RETENTION_DAYS=30
```

## Suggested Cadence

-   Daily: health check, backup status.
-   Weekly: ingestion status, embedding status, rate limit event volume.
-   Monthly: restore test, secret access review, dependency update review.

## Ingestion Record Template

``` text
Date:
Operator:
Command(s):
Source slug(s):
Ingestion status output:
Embedding status output:
Smoke tests passed:
Notes:
```
