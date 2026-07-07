# NYC Housing RAG

FastAPI skeleton for the NYC Housing Law RAG MVP.

## Local Setup

1. Create a virtual environment and install dependencies.

``` text
uv sync --extra dev
```

2. Create local environment settings.

``` text
cp .env.example .env
```

3. Start or configure PostgreSQL, then run migrations.

``` text
uv run alembic upgrade head
```

4. Start the application.

``` text
uv run uvicorn app.main:app --reload
```

5. Run tests.

``` text
uv run pytest
```

## Environment Variables

- `APP_ENV`: `local`, `staging`, or `production`
- `APP_NAME`: application display name
- `APP_HOST`: local bind host
- `APP_PORT`: local bind port
- `LOG_LEVEL`: Python logging level
- `DATABASE_URL`: SQLAlchemy PostgreSQL URL
- `DATABASE_POOL_SIZE`: base database pool size
- `DATABASE_MAX_OVERFLOW`: additional overflow connections
- `HEALTHCHECK_TIMEOUT_SECONDS`: database health check timeout
- `SESSION_COOKIE_NAME`: name of the HTTP-only session cookie
- `SESSION_COOKIE_SECURE`: set to `true` in production
- `SESSION_COOKIE_SAMESITE`: `lax`, `strict`, or `none`
- `SESSION_TTL_HOURS`: session lifetime in hours
- `PASSWORD_MIN_LENGTH`: minimum password length
- `ARTIFACT_STORAGE_BACKEND`: artifact storage backend, currently `local`
- `ARTIFACT_STORAGE_PATH`: local raw artifact directory
- `ARTIFACT_S3_BUCKET`: S3-compatible bucket for artifact storage
- `ARTIFACT_S3_PREFIX`: optional S3 key prefix
- `ARTIFACT_S3_REGION`: optional S3 region
- `ARTIFACT_S3_ENDPOINT_URL`: optional S3-compatible endpoint URL
- `ARTIFACT_S3_ACCESS_KEY_ID`: optional S3 access key
- `ARTIFACT_S3_SECRET_ACCESS_KEY`: optional S3 secret key
- `INGESTION_HTTP_TIMEOUT_SECONDS`: HTTP timeout for source downloads
- `INGESTION_HTTP_MAX_RETRIES`: retry count for source downloads
- `INGESTION_USER_AGENT`: user agent for public source downloads
- `HPD_VIOLATIONS_LIMIT`: maximum HPD violation records loaded per run
- `EMBEDDING_PROVIDER`: `fake`, `openai`, or `openai_compatible`
- `EMBEDDING_API_KEY`: embedding provider API key
- `EMBEDDING_BASE_URL`: embedding provider base URL
- `EMBEDDING_TIMEOUT_SECONDS`: embedding provider timeout
- `EMBEDDING_MAX_RETRIES`: embedding provider retry count
- `ANSWER_LLM_PROVIDER`: answer provider, currently `fake`
- `ANSWER_LLM_MODEL`: answer model name
- `ANSWER_LLM_API_KEY`: reserved for a future paid provider
- `ANSWER_LLM_BASE_URL`: answer provider base URL
- `ANSWER_LLM_TIMEOUT_SECONDS`: answer provider timeout
- `ANSWER_LLM_MAX_RETRIES`: answer provider retry count
- `ANSWER_MAX_CONTEXT_CHUNKS`: maximum retrieved chunks sent to answer generation
- `ANSWER_MAX_CONTEXT_CHARS`: maximum retrieved text characters sent to answer generation
- `ANSWER_MAX_OUTPUT_TOKENS`: maximum provider output tokens
- `ANSWER_TEMPERATURE`: generation temperature
- `ANSWER_INCLUDE_SOURCE_COVERAGE`: include public-source coverage disclosure
- `RATE_LIMIT_ENABLED`: enable database-backed abuse controls
- `LOGIN_FAILED_LIMIT`: failed login attempts allowed per IP window
- `LOGIN_FAILED_WINDOW_SECONDS`: failed login rolling window
- `LOGIN_COOLDOWN_SECONDS`: cooldown hint for failed login throttling
- `SEARCH_REQUESTS_PER_HOUR`: default per-user search limit
- `ANSWER_REQUESTS_PER_HOUR`: default per-user answer limit
- `USER_DAILY_LLM_TOKEN_BUDGET`: default per-user daily answer token budget
- `MAX_REQUEST_BODY_BYTES`: maximum JSON API request body size
- `SEARCH_TIMEOUT_SECONDS`: search endpoint timeout threshold
- `ANSWER_TIMEOUT_SECONDS`: answer endpoint timeout threshold
- `RATE_LIMIT_EVENT_RETENTION_DAYS`: old event retention window
- `ALLOW_FAKE_PROVIDERS_IN_PRODUCTION`: explicit production override for fake providers

Do not commit `.env` or production secrets.

## Health Check

``` text
GET /health
```

Healthy response:

``` json
{
  "status": "ok",
  "database": "ok"
}
```

Unhealthy response:

``` json
{
  "status": "error",
  "database": "error"
}
```

## Deployment

Detailed operational procedures live in:

- [Deployment](docs/Runbooks/Deployment.md)
- [Backup and Restore](docs/Runbooks/Backup_And_Restore.md)
- [Ingestion and Maintenance](docs/Runbooks/Ingestion.md)
- [Monitoring and Error Logging](docs/Runbooks/Monitoring.md)
- [Rollback](docs/Runbooks/Rollback.md)

Run the app with:

``` text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Required production settings:

- `APP_ENV=production`
- `DATABASE_URL` set through the hosting provider's secret manager
- `SESSION_COOKIE_SECURE=true`
- `RATE_LIMIT_ENABLED=true`

Production traffic must be served only over HTTPS. Do not expose secrets,
provider API keys, object storage credentials, or copied `.env` values in logs
or client-side code.

Run migrations as a release step or documented manual step before serving
traffic:

``` text
uv run alembic upgrade head
```

Before production traffic, enable database backups and complete one restore
test using [Backup and Restore](docs/Runbooks/Backup_And_Restore.md).

## Authentication

The MVP uses private application accounts. Public signup is intentionally
not available.

Create an admin user:

``` text
uv run python -m app.cli.users create-admin --email admin@example.com
```

Create a normal user:

``` text
uv run python -m app.cli.users create-user --email user@example.com
```

The commands prompt for a password without echoing it. Do not pass
passwords as command-line arguments.

Login:

``` text
curl -i -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your password"}' \
  http://localhost:8000/auth/login
```

Check current user:

``` text
curl -i -b cookies.txt http://localhost:8000/auth/me
```

Logout:

``` text
curl -i -b cookies.txt -c cookies.txt \
  -X POST http://localhost:8000/auth/logout
```

For production, set `SESSION_COOKIE_SECURE=true` and serve the application
only over HTTPS.

## Ingestion

The MVP only ingests freely accessible public sources. Paid legal
databases, proprietary summaries, commercial headnotes, paid citators, and
paywalled source material are not acceptable corpus sources.

Raw source artifacts are stored under `.artifacts/` by default. This
directory is ignored by git and should not be served publicly.

Apply migrations:

``` text
uv run alembic upgrade head
```

Seed the source registry:

``` text
uv run python -m app.cli.ingest seed-sources
```

Download a source:

``` text
uv run python -m app.cli.ingest download-source nyc-housing-maintenance-code
```

Parse the latest downloaded version of a source:

``` text
uv run python -m app.cli.ingest parse-source nyc-housing-maintenance-code
```

Download and parse a legal/guidance source in one command:

``` text
uv run python -m app.cli.ingest ingest-source nyc-housing-maintenance-code
```

Load HPD violations:

``` text
uv run python -m app.cli.ingest load-hpd-violations
```

Run the full MVP ingestion:

``` text
uv run python -m app.cli.ingest ingest-mvp
```

Ingest only sources that do not yet have loaded records:

``` text
uv run python -m app.cli.ingest ingest-missing
```

`ingest-missing` continues across configured sources. If a public source blocks
or moves, the command records the failed source in `ingestion_runs`, prints a
warning, and keeps the successfully ingested sources available. The NYC Housing
Maintenance Code source may return `403 Forbidden` from the public AmLegal URL
in local development because the site may serve an anti-bot challenge to CLI
HTTP clients. Keep the official AmLegal source configured, do not use
unofficial mirrors, and use the recorded warning as source-availability
evidence unless a permitted accessible official source is configured.

Show ingestion status counts:

``` text
uv run python -m app.cli.ingest status
```

Verify source-version artifacts and chunk traceability:

``` text
uv run python -m app.cli.ingest verify-traceability
```

## Retrieval

Phase 4 adds retrieval over ingested chunks. `/search` returns retrieved
source chunks only; it does not generate legal answers or legal advice.
Retrieval excludes inactive, restricted, or non-public source records.

On PostgreSQL, keyword search uses `to_tsvector` / `plainto_tsquery`.
When the `vector` extension is available, migrations add a pgvector-backed
`embedding_vector` column for vector search. SQLite and non-vector
development databases keep the portable JSON embedding fallback.

Generate embeddings for all chunks:

``` text
uv run python -m app.cli.embeddings generate
```

Generate embeddings for one source:

``` text
uv run python -m app.cli.embeddings generate --source-slug nyc-housing-maintenance-code
```

Check embedding coverage:

``` text
uv run python -m app.cli.embeddings status
```

Search requires authentication. Login first:

``` text
curl -i -c cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@example.com","password":"your password"}' \
  http://localhost:8000/auth/login
```

Run a search:

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"query":"NYC Admin Code § 27-2005","limit":5}' \
  http://localhost:8000/search
```

Supported search filters:

- `source_type`
- `jurisdiction`
- `source_slug`
- `source_id`
- `document_id`

Embedding defaults:

- `EMBEDDING_PROVIDER=fake`
- `EMBEDDING_MODEL=fake-small`
- `EMBEDDING_DIMENSION=16`
- `EMBEDDING_BATCH_SIZE=64`

For a real OpenAI-compatible embedding provider:

- `EMBEDDING_PROVIDER=openai`
- `EMBEDDING_MODEL=<provider embedding model>`
- `EMBEDDING_API_KEY=<server-side key>`
- `EMBEDDING_BASE_URL=https://api.openai.com/v1`
- `EMBEDDING_DIMENSION=<provider vector dimension>`

Search defaults:

- `SEARCH_DEFAULT_LIMIT=10`
- `SEARCH_MAX_LIMIT=25`
- `SEARCH_VECTOR_WEIGHT=0.45`
- `SEARCH_KEYWORD_WEIGHT=0.35`
- `SEARCH_CITATION_WEIGHT=1.0`

## HPD Violation Lookup

HPD violation lookup is available for authenticated users:

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"house_number":"123","street_name":"MAIN STREET"}' \
  http://localhost:8000/hpd/violations/search
```

Supported lookup fields:

- `building_id`
- `registration_id`
- `house_number` plus `street_name`
- `zip_code`
- `boro`
- `violation_class`
- `current_status`

The current HPD dataset model does not include BBL. Use building ID,
registration ID, or address fields for property-specific violation lookups.

## Answer Generation

Phase 5 adds citation-grounded answer generation. `/answer` runs hybrid
retrieval, sends only retrieved chunks to the configured answer provider,
validates citations against those retrieved chunk IDs, and logs the answer
attempt. The default provider is deterministic and local:

- `ANSWER_LLM_PROVIDER=fake`
- `ANSWER_LLM_MODEL=fake-answer-small`

For a real OpenAI-compatible answer provider:

- `ANSWER_LLM_PROVIDER=openai`
- `ANSWER_LLM_MODEL=<provider chat model>`
- `ANSWER_LLM_API_KEY=<server-side key>`
- `ANSWER_LLM_BASE_URL=https://api.openai.com/v1`

The endpoint requires authentication. Login first, then call:

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"question":"What does NYC Admin Code § 27-2005 require?","limit":5}' \
  http://localhost:8000/answer
```

Supported answer filters match search filters:

- `source_type`
- `jurisdiction`
- `source_slug`
- `source_id`
- `document_id`

Answers include source-backed citation objects, a source coverage disclosure,
and the legal information disclaimer. Unsupported questions return
`answer_status="unsupported"` instead of a guessed answer.

## Routed Query API

`/query` performs deterministic routing for authenticated users:

- Legal questions route to answer generation.
- Property/HPD questions with an address, building ID, or registration ID route
  to HPD violation lookup.

``` text
curl -i -b cookies.txt \
  -H "Content-Type: application/json" \
  -d '{"question":"Show HPD violations at 123 MAIN STREET"}' \
  http://localhost:8000/query
```

The router is deterministic and intentionally simple; it is not an LLM
classifier.

## Rate Limiting and Abuse Controls

Phase 6 adds PostgreSQL-backed abuse controls:

- Failed login attempts are limited per IP.
- Authenticated `/search` requests are limited per user.
- Authenticated `/answer` requests are limited per user.
- Daily answer token budgets are enforced before answer generation.
- Oversized API requests return `413 Payload Too Large`.
- Slow search or answer requests return `504 Gateway Timeout`.

Default limits:

- `LOGIN_FAILED_LIMIT=5` per `LOGIN_FAILED_WINDOW_SECONDS=900`
- `SEARCH_REQUESTS_PER_HOUR=120`
- `ANSWER_REQUESTS_PER_HOUR=30`
- `USER_DAILY_LLM_TOKEN_BUDGET=30000`
- `MAX_REQUEST_BODY_BYTES=65536`
- `SEARCH_TIMEOUT_SECONDS=10`
- `ANSWER_TIMEOUT_SECONDS=30`

Prune old rate limit events:

``` text
uv run python -m app.cli.limits prune-events
```

Production hardening rejects `APP_ENV=production` with fake providers unless
`ALLOW_FAKE_PROVIDERS_IN_PRODUCTION=true` is set explicitly. Use real providers
for production-quality answer and semantic retrieval behavior.

## Operational Commands

Run deployed API smoke tests:

``` text
uv run python -m app.cli.smoke --base-url https://example.com --email admin@example.com --password '<password>'
```

Run local MVP route and corpus smoke checks:

``` text
uv run python -m app.cli.smoke --email admin@example.com --password '<password>' --mvp-eval
```

Evaluate sample questions against the current corpus:

``` text
uv run python -m app.cli.evaluate --user-email admin@example.com
```

Summarize logs and rate limit events:

``` text
uv run python -m app.cli.ops log-summary
uv run python -m app.cli.ops rate-limits
uv run python -m app.cli.ops source-freshness
```
