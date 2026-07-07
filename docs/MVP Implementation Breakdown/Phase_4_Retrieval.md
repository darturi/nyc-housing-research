# Phase 4: Retrieval Implementation

## Goal

Build the MVP retrieval layer on top of the Phase 3 corpus. Phase 4 should
support exact citation lookup, PostgreSQL full-text keyword search,
embedding generation for chunks, vector search with `pgvector`, metadata
filters, result merging, and retrieval logging.

This phase should return relevant cited chunks and source links. It should
not generate final legal answers. LLM answer synthesis belongs to Phase 5.

## Scope

### Included

-   Exact citation lookup using `citations` and `chunks`
-   PostgreSQL full-text search over `chunks.text`, `chunks.title`, and
    citation text
-   Embeddings for retrieval-ready chunks
-   Vector search using `pgvector`
-   Hybrid result merging across exact, keyword, and vector retrieval
-   Metadata filters for source type, jurisdiction, and source slug
-   Auth-protected search endpoint
-   Retrieval logs that record user, query, filters, returned chunks, and
    retrieval mode
-   Tests for citation lookup, keyword search, vector search, ranking, and
    logging

### Explicitly Deferred

-   LLM answer generation
-   Legal disclaimer rendering
-   Reranking with a paid/model API
-   OpenSearch
-   Knowledge graph traversal
-   Case law retrieval
-   Public unauthenticated search
-   Rate limiting and cost budgets, except where needed to protect
    embedding generation commands

## Data Dependencies

Phase 4 assumes Phase 3 has already created:

-   `sources`
-   `source_versions`
-   `documents`
-   `sections`
-   `chunks`
-   `citations`
-   `hpd_violations`

Search should only return chunks that can be traced back to:

-   Source
-   Source version
-   Source URL
-   Citation, when available

## Deliverables

-   `pgvector` extension migration
-   `chunk_embeddings` table
-   `retrieval_logs` table
-   Search service module
-   Embedding provider abstraction
-   Embedding generation CLI
-   Hybrid retrieval implementation
-   Auth-protected `POST /search` endpoint
-   Search response schemas
-   Tests and fixtures
-   README updates
-   Work log entry after implementation

## Design Principles

### Retrieval Before Generation

Phase 4 should produce structured search results only. The response should
include enough information for Phase 5 to generate answers without citing
anything outside the retrieved chunk IDs.

### Exact Citations First

If the user query contains an exact citation, exact citation results should
be ranked above semantic or keyword results. For legal search, citation
precision matters more than broad semantic similarity.

### Public-Source Traceability

Every returned result must expose its source name, source URL, source type,
jurisdiction, source version ID, chunk ID, and citation when available.

### Provider Swappability

Embedding generation should use a provider abstraction. The MVP may use a
paid embedding API, but the database and retrieval service should not be
tightly coupled to one model provider.

### Deterministic Tests

Default tests should not call paid APIs. Use a fake deterministic embedding
provider in tests.

## Suggested Files

``` text
app/
├── api/
│   └── search.py
├── cli/
│   └── embeddings.py
├── models/
│   ├── chunk_embedding.py
│   └── retrieval_log.py
├── retrieval/
│   ├── __init__.py
│   ├── citations.py
│   ├── embeddings.py
│   ├── hybrid.py
│   ├── keyword.py
│   ├── logging.py
│   ├── schemas.py
│   └── vector.py
└── schemas/
    └── search.py
tests/
├── test_retrieval_citations.py
├── test_retrieval_keyword.py
├── test_retrieval_vector.py
├── test_retrieval_hybrid.py
├── test_retrieval_api.py
└── test_embedding_cli.py
```

## Database Changes

### Enable `pgvector`

Add an Alembic migration:

``` sql
CREATE EXTENSION IF NOT EXISTS vector;
```

For SQLite tests, skip extension creation or isolate it to PostgreSQL-only
migrations/tests.

### `chunk_embeddings`

Columns:

-   `id`: UUID primary key
-   `chunk_id`: foreign key to `chunks.id`
-   `embedding_model`: string
-   `embedding_provider`: string
-   `embedding_dimension`: integer
-   `embedding`: vector
-   `text_hash`: string
-   `created_at`: timestamp
-   `updated_at`: timestamp

Constraints and indexes:

-   Unique `(chunk_id, embedding_model)`
-   Vector index after enough rows exist
-   Index on `text_hash`
-   Index on `embedding_model`

Behavior:

-   If chunk text changes, regenerate the embedding because `text_hash`
    will differ.
-   Store one embedding per chunk per model.

### `retrieval_logs`

Columns:

-   `id`: UUID primary key
-   `user_id`: nullable foreign key to `users.id`
-   `query_text`: text
-   `query_hash`: SHA-256 hash of normalized query
-   `filters`: JSON
-   `retrieval_mode`: `citation`, `keyword`, `vector`, or `hybrid`
-   `returned_chunk_ids`: JSON array
-   `result_count`: integer
-   `latency_ms`: integer
-   `created_at`: timestamp

Notes:

-   Consider storing full query text for MVP debugging.
-   If privacy concerns increase, make query text retention configurable and
    keep only `query_hash`.

## Environment Variables

Add to `.env.example`:

``` text
EMBEDDING_PROVIDER=fake
EMBEDDING_MODEL=fake-small
EMBEDDING_DIMENSION=16
EMBEDDING_BATCH_SIZE=64
SEARCH_DEFAULT_LIMIT=10
SEARCH_MAX_LIMIT=25
SEARCH_VECTOR_WEIGHT=0.45
SEARCH_KEYWORD_WEIGHT=0.35
SEARCH_CITATION_WEIGHT=1.0
```

Later, if using a paid embedding API:

``` text
EMBEDDING_API_KEY=
```

Keep provider API keys server-side only.

## Search Request and Response

### `POST /search`

Authentication:

-   Requires a valid Phase 2 session.

Request:

``` json
{
  "query": "What does NYC Admin Code § 27-2005 require?",
  "limit": 10,
  "filters": {
    "source_type": "law",
    "jurisdiction": "NYC",
    "source_slug": "nyc-housing-maintenance-code"
  }
}
```

Response:

``` json
{
  "query": "What does NYC Admin Code § 27-2005 require?",
  "retrieval_mode": "hybrid",
  "results": [
    {
      "chunk_id": "uuid",
      "document_id": "uuid",
      "source_id": "uuid",
      "source_version_id": "uuid",
      "source_name": "NYC Housing Maintenance Code",
      "source_type": "law",
      "jurisdiction": "NYC",
      "source_url": "https://...",
      "citation": "NYC Admin Code § 27-2005",
      "title": "Duties of owner",
      "text": "chunk text",
      "score": 1.0,
      "match_type": "citation"
    }
  ]
}
```

Validation:

-   `query` required
-   Reject empty or whitespace-only query
-   Enforce maximum query length
-   Clamp `limit` to `SEARCH_MAX_LIMIT`
-   Ignore or reject unsupported filters

## Step 1: Add Retrieval Models and Migration

Create:

-   `ChunkEmbedding`
-   `RetrievalLog`

Add migration for:

-   `pgvector` extension
-   `chunk_embeddings`
-   `retrieval_logs`

Acceptance criteria:

-   Migration applies to PostgreSQL
-   Tests can run with a non-vector fallback or isolated fake storage
-   Retrieval logs can store returned chunk IDs

## Step 2: Add Embedding Provider Abstraction

Implement `app/retrieval/embeddings.py`.

Interface:

``` text
embed_text(text: str) -> list[float]
embed_batch(texts: list[str]) -> list[list[float]]
```

Providers:

-   `FakeEmbeddingProvider` for tests and local deterministic behavior
-   Optional production provider placeholder for paid APIs

Fake provider requirements:

-   Deterministic
-   Fixed dimension from settings
-   No network calls
-   Good enough to test vector plumbing, not semantic quality

Acceptance criteria:

-   Tests do not require paid API keys
-   Embedding dimension matches config
-   Provider errors are clear and do not leak secrets

## Step 3: Add Embedding Generation CLI

Implement `app/cli/embeddings.py`.

Commands:

``` text
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings generate --source-slug nyc-housing-maintenance-code
uv run python -m app.cli.embeddings status
```

Behavior:

-   Find chunks without embeddings for the configured model
-   Regenerate embeddings when `chunks.text_hash` differs from stored
    embedding `text_hash`
-   Process chunks in batches
-   Print created, updated, skipped counts

Acceptance criteria:

-   Running generation twice is idempotent
-   Changed chunk text triggers embedding update
-   Status shows embedded and missing chunk counts

## Step 4: Exact Citation Lookup

Implement `app/retrieval/citations.py`.

Behavior:

-   Extract citation candidates from the query using existing citation
    normalization utilities
-   Match `citations.normalized_citation`
-   Join to `chunks`, `documents`, `sources`, and `source_versions`
-   Return citation matches with high score

Ranking:

-   Exact normalized citation match gets top score
-   Citation lookup should run before keyword/vector search

Acceptance criteria:

-   Query `§ 27-2005` returns the matching HMC chunk first
-   Query `NYC Admin Code § 27-2005` returns same normalized match
-   Citation results include source URL

## Step 5: PostgreSQL Full-Text Keyword Search

Implement `app/retrieval/keyword.py`.

MVP approach:

-   Use PostgreSQL `to_tsvector` / `plainto_tsquery`
-   Search `chunks.title`, `chunks.citation`, and `chunks.text`
-   Weight title/citation above body text

If SQLite is used in tests:

-   Use a simple case-insensitive fallback search for unit tests

Acceptance criteria:

-   Keyword query returns relevant chunks
-   Source/jurisdiction filters work
-   Returned results include citations and source URLs

## Step 6: Vector Search

Implement `app/retrieval/vector.py`.

Behavior:

-   Embed the query using configured embedding provider
-   Search `chunk_embeddings.embedding`
-   Join to chunk/source metadata
-   Return vector distance converted into a score

MVP notes:

-   Use exact vector search first.
-   Add ANN vector index later when corpus size requires it.

Acceptance criteria:

-   Vector search returns chunks with embeddings
-   Missing embeddings produce a clear empty result, not an exception
-   Filters work consistently with keyword search

## Step 7: Hybrid Retrieval

Implement `app/retrieval/hybrid.py`.

Inputs:

-   Query text
-   Limit
-   Filters

Process:

1.  Run citation lookup.
2.  Run keyword search.
3.  Run vector search.
4.  Deduplicate by `chunk_id`.
5.  Combine scores using configured weights.
6.  Boost exact citation matches.
7.  Return top `limit`.

Scoring:

-   Citation score: high deterministic boost
-   Keyword score: normalized rank
-   Vector score: normalized similarity
-   Final score: weighted combination

Acceptance criteria:

-   Exact citation match outranks keyword/vector-only matches
-   Duplicate chunk results are merged
-   Hybrid result ordering is deterministic for equal scores

## Step 8: Metadata Filters

Supported filters:

-   `source_type`
-   `jurisdiction`
-   `source_slug`
-   `source_id`
-   `document_id`

Rules:

-   Apply filters consistently across citation, keyword, and vector search
-   Reject unsupported filter keys
-   Do not allow arbitrary SQL fragments

Acceptance criteria:

-   Filtering to `jurisdiction=NYC` excludes NY-only sources
-   Filtering by source slug returns only that source
-   Unsupported filter key returns validation error

## Step 9: Retrieval Logging

Implement `app/retrieval/logging.py`.

Log:

-   User ID
-   Query text
-   Query hash
-   Filters
-   Retrieval mode
-   Returned chunk IDs
-   Result count
-   Latency

Acceptance criteria:

-   Every `/search` request creates one retrieval log
-   Logs contain returned chunk IDs
-   Logs do not contain API keys or secrets

## Step 10: Add Search API

Implement `app/api/search.py`.

Route:

``` text
POST /search
```

Requirements:

-   Requires authenticated user
-   Validates request body
-   Calls hybrid retrieval service
-   Logs retrieval
-   Returns structured results

Acceptance criteria:

-   Anonymous requests return `401`
-   Authenticated requests return structured search results
-   Search response includes citations and source links

## Step 11: Tests

Minimum tests:

-   Embedding provider is deterministic
-   Embedding generation is idempotent
-   Changed chunk text updates embeddings
-   Exact citation lookup finds normalized citation
-   Citation lookup outranks keyword/vector matches
-   Keyword search finds chunk text
-   Vector search returns embedded chunks
-   Hybrid search deduplicates chunk IDs
-   Filters apply across retrieval modes
-   Unsupported filter keys are rejected
-   `/search` requires authentication
-   `/search` logs returned chunk IDs

Use Phase 3 fixture data to create test chunks and citations.

## Step 12: Documentation

Update `README.md` with:

-   New environment variables
-   Embedding generation commands
-   Search API example
-   Note that `/search` returns retrieved chunks only, not legal advice
-   Note that Phase 5 will add answer generation

Add a work-log entry after implementation.

## Security Checklist

-   `/search` requires authentication
-   Embedding API keys stay server-side
-   Query length is bounded
-   Search limit is bounded
-   Unsupported filters are rejected
-   No arbitrary SQL from request filters
-   Retrieval logs do not include secrets
-   Results include only public-source corpus data
-   Search does not claim paid legal databases were searched

## Manual Validation

After implementation:

1.  Apply migrations.

``` text
uv run alembic upgrade head
```

2.  Ensure Phase 3 data exists.

``` text
uv run python -m app.cli.ingest status
```

3.  Generate embeddings.

``` text
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings status
```

4.  Start the app.

``` text
uv run uvicorn app.main:app --reload
```

5.  Login using Phase 2 auth and save cookies.

``` text
curl -i -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your password"}' \
  http://localhost:8000/auth/login
```

6.  Search exact citation.

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"query":"NYC Admin Code § 27-2005","limit":5}' \
  http://localhost:8000/search
```

7.  Search keyword.

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"query":"owner must keep premises in good repair","limit":5}' \
  http://localhost:8000/search
```

Expected:

-   Authenticated searches return `200`
-   Unauthenticated searches return `401`
-   Citation queries rank exact citation matches first
-   Results include source URLs
-   Retrieval logs are created

## Completion Criteria

Phase 4 is complete when:

-   `pgvector` support is available in PostgreSQL
-   Chunks can be embedded idempotently
-   Exact citation lookup works
-   Keyword search works
-   Vector search works
-   Hybrid retrieval merges and ranks results deterministically
-   Metadata filters work
-   `POST /search` is authenticated
-   Search results include citations and source links
-   Retrieval logs record returned chunk IDs
-   Tests pass
-   README and work log are updated

## Handoff to Phase 5

Phase 5 should consume Phase 4 retrieval results. Answer generation must
only cite chunk IDs returned by Phase 4 retrieval logs or the direct search
response.
