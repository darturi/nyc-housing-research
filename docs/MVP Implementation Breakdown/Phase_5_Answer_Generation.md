# Phase 5: Answer Generation Implementation

## Goal

Build the MVP answer-generation layer on top of Phase 4 retrieval. Phase 5
should turn authenticated user questions into citation-grounded answers by
retrieving relevant chunks, sending only those chunks to an LLM provider,
enforcing strict citation rules, refusing unsupported answers, showing source
coverage limits, adding a legal disclaimer, and logging generation metadata.

This phase should produce final user-facing answers. It should not add public
access, rate limiting, advanced reranking, case law retrieval, OpenSearch, or a
knowledge graph. Those belong to later phases.

## Scope

### Included

-   Auth-protected answer endpoint
-   Answer service that calls Phase 4 hybrid retrieval
-   LLM provider abstraction with deterministic fake provider for tests
-   Prompt builder with strict citation and limitation rules
-   Citation validation against retrieved chunk IDs only
-   Unsupported-answer refusal behavior
-   Source coverage disclosure for known omitted or unavailable sources
-   Always-present legal information disclaimer
-   `answer_logs` table with retrieval, model, token, latency, and answer data
-   Tests for prompt construction, answer API behavior, citation validation,
    refusal handling, and logging

### Explicitly Deferred

-   Public unauthenticated answers
-   Rate limiting, token budgets, and abuse controls
-   Streaming responses
-   Conversation memory
-   User feedback and answer rating UI
-   Advanced model reranking
-   Citation checking against sources that were not retrieved
-   Case law retrieval
-   Paid legal research data
-   Admin dashboard for answer logs

## Data Dependencies

Phase 5 assumes Phase 4 has already created and tested:

-   `chunks`
-   `citations`
-   `chunk_embeddings`
-   `retrieval_logs`
-   Hybrid retrieval through `app.retrieval.hybrid.hybrid_search`
-   Auth-protected `POST /search`

Answers must only cite chunks returned by retrieval for the current request.
If no retrieved chunk supports the requested legal conclusion, the system
should say that the corpus does not support an answer instead of guessing.

## Deliverables

-   `answer_logs` table and Alembic migration
-   Answer request and response schemas
-   LLM provider abstraction
-   Fake deterministic LLM provider for local development and tests
-   Prompt construction module
-   Citation validation module
-   Answer generation service
-   Auth-protected `POST /answer` endpoint
-   Environment settings for answer generation and provider configuration
-   Tests and fixtures
-   README updates
-   Work log entry after implementation

## Design Principles

### Retrieval-Grounded Only

The answer generator may use the LLM to synthesize language, but not to add
new legal authority. The model receives a bounded list of retrieved chunks and
must cite only those chunks.

### Refuse When Unsupported

If the retrieved chunks do not answer the question, the response should be a
clear limitation message. It should still include:

-   What was searched
-   Why the answer is limited
-   Any partially relevant cited material, if useful
-   The standard legal information disclaimer

### Preserve Source Traceability

Every citation shown in an answer should map back to a retrieved chunk, source,
source version, and public source URL. Phase 5 should not create free-form
citations that cannot be resolved to a chunk ID.

### Provider Swappability

LLM calls should sit behind a provider interface. The MVP may use a paid LLM
API in production, but tests and local development should work without network
access or paid keys.

### Deterministic Tests

Default tests should not call paid APIs. The fake LLM provider should return
predictable answers from supplied context so API, logging, refusal, and
citation-validation behavior can be tested reliably.

## Suggested Files

``` text
app/
├── api/
│   └── answers.py
├── answer/
│   ├── __init__.py
│   ├── citations.py
│   ├── logging.py
│   ├── prompts.py
│   ├── providers.py
│   ├── schemas.py
│   └── service.py
├── models/
│   └── answer_log.py
└── schemas/
    └── answer.py
tests/
├── test_answer_api.py
├── test_answer_citations.py
├── test_answer_logging.py
├── test_answer_prompts.py
└── test_answer_service.py
```

## Database Changes

### `answer_logs`

Columns:

-   `id`: UUID primary key
-   `user_id`: nullable foreign key to `users.id`
-   `retrieval_log_id`: nullable foreign key to `retrieval_logs.id`
-   `question_text`: text
-   `question_hash`: SHA-256 hash of normalized question
-   `filters`: JSON
-   `retrieved_chunk_ids`: JSON array
-   `cited_chunk_ids`: JSON array
-   `answer_text`: text
-   `answer_status`: `answered`, `unsupported`, or `provider_error`
-   `llm_provider`: string
-   `llm_model`: string
-   `prompt_token_count`: nullable integer
-   `completion_token_count`: nullable integer
-   `total_token_count`: nullable integer
-   `latency_ms`: integer
-   `error_message`: nullable text
-   `created_at`: timestamp

Indexes:

-   `user_id`
-   `question_hash`
-   `answer_status`
-   `created_at`

Notes:

-   Store full answer text for MVP debugging and auditability.
-   Keep the schema compatible with later retention controls.
-   `retrieval_log_id` should link the generated answer to the exact retrieved
    chunk list when available.

## Environment Variables

Add to `.env.example`:

``` text
ANSWER_LLM_PROVIDER=fake
ANSWER_LLM_MODEL=fake-answer-small
ANSWER_MAX_CONTEXT_CHUNKS=8
ANSWER_MAX_CONTEXT_CHARS=16000
ANSWER_MAX_OUTPUT_TOKENS=900
ANSWER_TEMPERATURE=0
ANSWER_INCLUDE_SOURCE_COVERAGE=true
```

Later, if using a paid LLM API:

``` text
ANSWER_LLM_API_KEY=
```

Keep provider API keys server-side only.

## Answer Request and Response

### `POST /answer`

Authentication:

-   Requires a valid Phase 2 session.

Request:

``` json
{
  "question": "What does NYC Admin Code § 27-2005 require an owner to do?",
  "limit": 8,
  "filters": {
    "source_type": "law",
    "jurisdiction": "NYC"
  }
}
```

Response:

``` json
{
  "question": "What does NYC Admin Code § 27-2005 require an owner to do?",
  "answer_status": "answered",
  "answer": "NYC Admin Code § 27-2005 requires an owner to keep a dwelling in good repair...",
  "citations": [
    {
      "chunk_id": "uuid",
      "citation": "NYC Admin Code § 27-2005",
      "source_name": "NYC Housing Maintenance Code",
      "source_url": "https://..."
    }
  ],
  "source_coverage": "This MVP searched the configured public NYC housing-law corpus. It does not include paid legal databases or unpublished materials.",
  "disclaimer": "This is legal information, not legal advice. Consult a qualified attorney for advice about a specific situation."
}
```

Unsupported response:

``` json
{
  "question": "What did a court hold in a specific unpublished housing case?",
  "answer_status": "unsupported",
  "answer": "The current corpus does not contain enough retrieved public-source material to answer that question.",
  "citations": [],
  "source_coverage": "This MVP does not include paid legal databases, proprietary case-law collections, or unpublished court materials.",
  "disclaimer": "This is legal information, not legal advice. Consult a qualified attorney for advice about a specific situation."
}
```

## Prompt Requirements

The prompt should include:

-   System role defining the assistant as a citation-grounded NYC housing-law
    information tool
-   Explicit instruction not to provide legal advice
-   User question
-   Retrieved chunks with stable chunk IDs, citations, source names, source
    URLs, titles, and text
-   Required answer format
-   Refusal rule when retrieved chunks do not support an answer
-   Instruction to cite only supplied chunk IDs
-   Source coverage statement

Required answer sections:

-   Direct answer
-   Relevant law or source-backed rule
-   Practical implications, if supported by retrieved chunks
-   Exceptions or limits, if supported by retrieved chunks
-   Citations
-   Disclaimer

The prompt should avoid asking the model to cite by raw URL alone. The model
should cite by chunk ID or citation token that the backend can validate and
then transform into the public response citation objects.

## Citation Validation

After receiving an LLM response:

1.  Extract cited chunk IDs or citation tokens from the structured provider
    output.
2.  Compare cited IDs against retrieved chunk IDs for the request.
3.  Drop or reject any citation that was not retrieved.
4.  If the answer depends on invalid citations, return an `unsupported`
    response or regenerate once with stricter instructions.
5.  Build public citation objects from retrieved `SearchResult` data rather
    than trusting model-provided source metadata.

For the MVP, prefer structured provider output over parsing prose citations.

## Answer Service Flow

1.  Validate request body.
2.  Convert filters with `SearchFilters.from_dict`.
3.  Run `hybrid_search`.
4.  Log retrieval or reuse a retrieval logger that returns `retrieval_log_id`.
5.  If retrieval returns no results, return `unsupported`.
6.  Trim context to `ANSWER_MAX_CONTEXT_CHUNKS` and
    `ANSWER_MAX_CONTEXT_CHARS`.
7.  Build prompt from retrieved chunks.
8.  Call configured LLM provider.
9.  Validate citations against retrieved chunk IDs.
10. Build answer response with citation objects, source coverage, and
    disclaimer.
11. Store `answer_logs`.

## Error Handling

-   Invalid filters: return `422 Unprocessable Entity`.
-   No supporting retrieval results: return `200 OK` with
    `answer_status = "unsupported"`.
-   LLM provider timeout or failure: return `503 Service Unavailable` and log
    `answer_status = "provider_error"`.
-   Invalid model citations: return `200 OK` with
    `answer_status = "unsupported"` unless a single retry is implemented and
    succeeds.
-   Oversized question: reuse the existing 4,000-character request limit.

## Testing Plan

### Unit Tests

-   Prompt builder includes question, chunk IDs, citations, source URLs, source
    coverage, refusal rule, and disclaimer rule.
-   Citation validator accepts only retrieved chunk IDs.
-   Citation validator rejects unknown, malformed, or duplicate citations.
-   Fake LLM provider returns deterministic answered and unsupported outputs.
-   Answer logger records retrieved and cited chunk IDs.

### API Tests

-   Anonymous users cannot call `POST /answer`.
-   Authenticated users receive an answer for supported fixture chunks.
-   Unsupported questions return `answer_status = "unsupported"`.
-   Responses always include disclaimer and source coverage.
-   Public citations are built from retrieved chunk metadata.
-   Provider failures are logged and return `503`.

### Integration Tests

-   `POST /answer` calls hybrid retrieval and logs both retrieval and answer
    data.
-   Answer generation cannot cite chunks outside the retrieved result set.
-   Existing `/search` behavior remains unchanged.

## Acceptance Criteria

-   Authenticated users can call `POST /answer`.
-   Answers include citations from retrieved chunks only.
-   Unsupported questions receive a clear limitation message instead of a
    guessed answer.
-   Legal information disclaimer is always present.
-   Source coverage disclosure is present when configured.
-   Every answer attempt is logged with user ID, retrieved chunk IDs, cited
    chunk IDs, provider, model, status, latency, and token usage when
    available.
-   Default tests pass without paid API calls.

## Implementation Order

1.  Add settings and `.env.example` entries.
2.  Add `answer_logs` model and migration.
3.  Add answer schemas.
4.  Add fake LLM provider and provider interface.
5.  Add prompt builder and citation validator.
6.  Add answer service and logging helper.
7.  Add authenticated `POST /answer` route.
8.  Add unit, API, and integration tests.
9.  Update README with answer endpoint and provider configuration.
10. Add Phase 5 work log after implementation.

## Manual Verification

After implementation, run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic upgrade head
uv run alembic current
```

If the database has live chunks and embeddings:

``` text
uv run python -m app.cli.embeddings status
```

Then manually test:

-   Login with a private user.
-   Call `POST /answer` with a citation-specific question.
-   Confirm cited chunk IDs came from the current retrieval result.
-   Call `POST /answer` with an unsupported case-law or paid-database
    question.
-   Confirm the answer refuses and explains source coverage limits.

## Next Step

After Phase 5 is complete, Phase 6 should add rate limiting, login-abuse
controls, answer request quotas, and daily LLM cost budgets so the private MVP
can safely use a paid generation provider.
