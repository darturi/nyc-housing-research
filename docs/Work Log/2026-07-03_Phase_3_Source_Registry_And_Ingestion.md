# Work Log: Phase 3 Source Registry and Ingestion

Date: 2026-07-03

## Summary

Implemented Phase 3 of the NYC Housing Law RAG MVP based on
`docs/MVP Implementation Breakdown/Phase_3_Source_Registry_And_Ingestion.md`.

This phase added the ingestion foundation: source registry tables, source
version hashing, ingestion run tracking, local artifact storage, legal
document parsing, citation normalization, HPD violation loading, CLI
commands, tests, and README documentation.

## Files Added

-   `app/cli/ingest.py`
-   `app/ingestion/__init__.py`
-   `app/ingestion/artifacts.py`
-   `app/ingestion/citations.py`
-   `app/ingestion/downloaders.py`
-   `app/ingestion/hpd_violations.py`
-   `app/ingestion/legal_text.py`
-   `app/ingestion/registry.py`
-   `app/ingestion/runners.py`
-   `app/models/chunk.py`
-   `app/models/citation.py`
-   `app/models/document.py`
-   `app/models/hpd_violation.py`
-   `app/models/ingestion_run.py`
-   `app/models/section.py`
-   `app/models/source.py`
-   `app/models/source_version.py`
-   `app/db/migrations/versions/20260703_0003_add_ingestion_tables.py`
-   `tests/fixtures/hmc_sample.html`
-   `tests/fixtures/hpd_violations_sample.json`
-   `tests/test_ingestion_artifacts.py`
-   `tests/test_ingestion_citations.py`
-   `tests/test_ingestion_hpd_violations.py`
-   `tests/test_ingestion_legal_text.py`
-   `tests/test_ingestion_registry.py`
-   `tests/test_ingestion_runs.py`

## Files Updated

-   `.env.example`
-   `.gitignore`
-   `README.md`
-   `pyproject.toml`
-   `uv.lock`
-   `app/core/config.py`
-   `app/models/__init__.py`

## Implementation Details

### Source Registry

Added `sources` and seeded the five MVP source records:

-   `nyc-housing-maintenance-code`
-   `ny-multiple-dwelling-law`
-   `ny-rpapl`
-   `hpd-guidance`
-   `hpd-violations`

Each source includes a public URL, publisher, jurisdiction, access type,
license/terms status, and notes.

### Source Versions and Artifacts

Added `source_versions` with SHA-256 content hashes and local artifact
URIs. Raw artifacts are stored under `.artifacts/`, which is now ignored by
git.

### Ingestion Runs

Added `ingestion_runs` to track mutation commands with started/succeeded/
failed status, counts, finish timestamps, and error messages.

### Corpus Tables

Added:

-   `documents`
-   `sections`
-   `chunks`
-   `citations`

Chunks are retrieval-ready but do not include embeddings yet. Embeddings
remain Phase 4 work.

### HPD Violations

Added `hpd_violations` with upsert behavior by external violation ID,
mapped address/status/date fields, and preserved raw JSON records.

### CLI Commands

Added:

``` text
uv run python -m app.cli.ingest seed-sources
uv run python -m app.cli.ingest download-source SOURCE_SLUG
uv run python -m app.cli.ingest parse-source SOURCE_SLUG
uv run python -m app.cli.ingest ingest-source SOURCE_SLUG
uv run python -m app.cli.ingest load-hpd-violations
uv run python -m app.cli.ingest ingest-mvp
uv run python -m app.cli.ingest status
```

### Tests

Added tests for:

-   Source registry idempotency
-   Source validation
-   Deterministic hashing
-   Artifact writing/reading
-   Source version deduplication
-   Citation normalization and extraction
-   Legal text parsing and idempotency
-   HPD violation insert/update behavior
-   Failed ingestion run recording

## Verification

Commands run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
uv run python -m app.cli.ingest seed-sources
uv run python -m app.cli.ingest status
```

Results:

-   `pytest`: passed, 28 tests.
-   `ruff check`: passed.
-   `alembic heads`: found `20260703_0003 (head)`.
-   `alembic upgrade head`: applied
    `20260701_0002 -> 20260703_0003`.
-   `alembic current`: confirmed `20260703_0003 (head)`.
-   `seed-sources`: succeeded.
-   `status`: showed 5 sources and 1 ingestion run in the local database.

## Known Notes

-   Live public-source downloads were not run during implementation.
    Download support exists, but the automated test suite uses fixtures to
    avoid network dependence.
-   The test suite still emits the existing FastAPI/Starlette `TestClient`
    deprecation warning from the dependency stack.
-   Phase 4 still needs retrieval, full-text search, embeddings, and answer
    generation.

## Next Step

Manually validate live ingestion with:

``` text
uv run python -m app.cli.ingest ingest-source nyc-housing-maintenance-code
uv run python -m app.cli.ingest load-hpd-violations
uv run python -m app.cli.ingest status
```

Then proceed to Phase 4 retrieval once live source ingestion behavior is
acceptable.
