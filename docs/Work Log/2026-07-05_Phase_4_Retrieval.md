# Work Log: Phase 4 Retrieval

Date: 2026-07-05

## Summary

Implemented Phase 4 of the NYC Housing Law RAG MVP based on
`docs/MVP Implementation Breakdown/Phase_4_Retrieval.md`.

This phase added the retrieval layer over Phase 3 chunks: exact citation
lookup, keyword search, deterministic fake embeddings, vector-style search,
hybrid result merging, authenticated `/search`, retrieval logs, embedding
CLI commands, tests, and README documentation.

## Files Added

-   `app/api/search.py`
-   `app/cli/embeddings.py`
-   `app/models/chunk_embedding.py`
-   `app/models/retrieval_log.py`
-   `app/retrieval/__init__.py`
-   `app/retrieval/citations.py`
-   `app/retrieval/common.py`
-   `app/retrieval/embeddings.py`
-   `app/retrieval/hybrid.py`
-   `app/retrieval/keyword.py`
-   `app/retrieval/logging.py`
-   `app/retrieval/schemas.py`
-   `app/retrieval/vector.py`
-   `app/schemas/search.py`
-   `app/db/migrations/versions/20260705_0004_add_retrieval_tables.py`
-   `tests/retrieval_fixtures.py`
-   `tests/test_embedding_cli.py`
-   `tests/test_retrieval_api.py`
-   `tests/test_retrieval_citations.py`
-   `tests/test_retrieval_hybrid.py`
-   `tests/test_retrieval_keyword.py`
-   `tests/test_retrieval_vector.py`

## Files Updated

-   `.env.example`
-   `README.md`
-   `app/core/config.py`
-   `app/main.py`
-   `app/models/__init__.py`

## Implementation Details

### Retrieval Tables

Added:

-   `chunk_embeddings`
-   `retrieval_logs`

The migration also attempts to enable PostgreSQL's `vector` extension when
it is available. The MVP stores embedding arrays in JSON so tests remain
portable and the plain local `postgres:16` image can still run the schema.

### Embeddings

Added a deterministic fake embedding provider for local development and
tests. It requires no paid API key and produces fixed-dimension vectors
from chunk text.

Added CLI commands:

``` text
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings generate --source-slug nyc-housing-maintenance-code
uv run python -m app.cli.embeddings status
```

### Retrieval Modes

Implemented:

-   Exact citation lookup using normalized citations
-   Keyword search over chunk title, citation, and text
-   Vector-style search over stored embeddings
-   Hybrid search with deterministic score merging and citation boosts

### Search API

Added authenticated endpoint:

``` text
POST /search
```

The endpoint validates query/filter input, runs hybrid retrieval, logs
returned chunk IDs, and returns structured source-backed chunks.

### Retrieval Logs

Every `/search` request records:

-   User ID
-   Query text and query hash
-   Filters
-   Retrieval mode
-   Returned chunk IDs
-   Result count
-   Latency

## Verification

Commands run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
uv run python -m app.cli.embeddings status
uv run python -m app.cli.ingest status
```

Results:

-   `pytest`: passed, 38 tests.
-   `ruff check`: passed.
-   `alembic heads`: found `20260705_0004 (head)`.
-   `alembic upgrade head`: applied
    `20260703_0003 -> 20260705_0004`.
-   `alembic current`: confirmed `20260705_0004 (head)`.
-   `embeddings status`: command succeeded; current database had
    `embedded: 0`, `missing: 0` because no live chunks had been ingested.
-   `ingest status`: command succeeded; current database had 5 seeded
    sources, 0 source versions, 0 documents, 0 chunks, 0 HPD violations,
    and 1 ingestion run.

## Known Notes

-   This phase does not generate final legal answers. `/search` returns
    retrieved chunks only.
-   The fake embedding provider is for local development and deterministic
    tests. A production embedding provider can be added later behind the
    same interface.
-   The test suite still emits the existing FastAPI/Starlette `TestClient`
    deprecation warning from the dependency stack.

## Next Step

Once Phase 3 live ingestion has populated chunks, run:

``` text
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings status
```

Then manually test authenticated `/search`. Phase 5 should consume these
retrieved chunk results for citation-grounded answer generation.
