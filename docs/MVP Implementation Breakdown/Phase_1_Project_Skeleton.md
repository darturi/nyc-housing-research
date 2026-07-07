# Phase 1: Project Skeleton Implementation

## Goal

Create the minimal application foundation for the NYC Housing Law RAG MVP.
This phase should produce a FastAPI service that boots locally, connects to
PostgreSQL, runs migrations from a clean database, exposes a health check,
loads configuration safely, emits structured logs, and is ready to deploy.

This phase does not implement authentication, ingestion, retrieval, or LLM
answer generation. It creates the structure those later phases will use.

## Deliverables

-   FastAPI application package
-   Configuration module
-   Database connection module
-   Alembic migration setup
-   Health check endpoint
-   Structured logging setup
-   Basic test harness
-   Local development instructions
-   Deployment configuration placeholder
-   Environment variable documentation

## Suggested Repository Structure

``` text
.
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   └── health.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   └── logging.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── session.py
│   │   └── migrations/
│   └── models/
│       └── __init__.py
├── alembic.ini
├── tests/
│   ├── __init__.py
│   └── test_health.py
├── .env.example
├── pyproject.toml
└── README.md
```

Adjust names to match the eventual project conventions, but keep the
boundaries: API routes, core configuration, database access, and models
should stay separate.

## Technology Choices

-   Python 3.11 or newer
-   FastAPI
-   Uvicorn
-   SQLAlchemy 2.x
-   Alembic
-   psycopg 3
-   Pydantic Settings
-   pytest
-   Ruff

Use async database access only if the rest of the project will commit to
async patterns. For the MVP, synchronous SQLAlchemy is acceptable and
simpler unless high concurrency is expected immediately.

## Environment Variables

Create `.env.example` with non-secret placeholder values:

``` text
APP_ENV=local
APP_NAME=nyc-housing-rag
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
DATABASE_URL=postgresql+psycopg://nyc_housing:nyc_housing@localhost:5432/nyc_housing
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
HEALTHCHECK_TIMEOUT_SECONDS=2
```

Do not commit real credentials. Later phases will add LLM, embedding,
object storage, and session secrets.

## Step 1: Initialize Python Project

Create or update `pyproject.toml` with runtime and development
dependencies.

Minimum runtime dependencies:

-   `fastapi`
-   `uvicorn`
-   `sqlalchemy`
-   `alembic`
-   `psycopg`
-   `pydantic-settings`

Minimum development dependencies:

-   `pytest`
-   `httpx`
-   `ruff`

Acceptance criteria:

-   Dependencies install cleanly
-   `python -m app.main` is not required; the app should run through
    Uvicorn
-   Tooling commands are documented in `README.md`

## Step 2: Build Configuration Module

Implement `app/core/config.py`.

Requirements:

-   Load settings from environment variables
-   Support local, staging, and production environments
-   Fail fast if required production settings are missing
-   Avoid printing secrets
-   Provide one importable settings object or dependency

Recommended settings:

-   `app_env`
-   `app_name`
-   `app_host`
-   `app_port`
-   `log_level`
-   `database_url`
-   `database_pool_size`
-   `database_max_overflow`
-   `healthcheck_timeout_seconds`

Security considerations:

-   Do not default production database credentials
-   Do not include secrets in `repr` output
-   Keep `.env` ignored by git
-   Keep `.env.example` safe to commit

Acceptance criteria:

-   App starts with `.env.example`-style local values
-   Missing `DATABASE_URL` produces a clear startup error
-   Tests can override settings without modifying real environment files

## Step 3: Create FastAPI Application

Implement `app/main.py`.

Requirements:

-   Create a FastAPI app instance
-   Register routers
-   Configure application name from settings
-   Add startup/shutdown hooks only if needed
-   Expose `/health`

Avoid adding authentication, CORS, or public API endpoints in this phase
unless needed for deployment health checks. Auth is Phase 2.

Acceptance criteria:

-   `GET /health` returns a JSON response
-   Unknown routes return normal FastAPI 404 responses
-   App can run locally with Uvicorn

## Step 4: Add Database Session Layer

Implement `app/db/session.py`.

Requirements:

-   Create SQLAlchemy engine from `DATABASE_URL`
-   Configure connection pool size from settings
-   Provide a session factory
-   Provide a small database connectivity helper for health checks

Recommended health check behavior:

-   Execute `SELECT 1`
-   Use a short timeout
-   Return failure without leaking database credentials

Acceptance criteria:

-   Database connection succeeds against a configured local or managed
    PostgreSQL instance
-   Failed database connection makes `/health` report an unhealthy status
-   Connection errors are logged without secrets

## Step 5: Set Up Alembic Migrations

Initialize Alembic and wire it to the project metadata.

Requirements:

-   Alembic reads the same `DATABASE_URL` as the app
-   `app/db/base.py` exposes SQLAlchemy metadata
-   Initial migration can run on a clean database
-   Migration command is documented

Initial migration:

-   It is acceptable for the first migration to create no application
    tables if no models exist yet
-   Prefer creating a tiny `schema_migrations_smoke_test` table only if the
    team wants a concrete migration artifact
-   Phase 2 will add `users` and `sessions`

Acceptance criteria:

-   `alembic upgrade head` runs successfully on an empty database
-   `alembic current` shows the expected revision
-   Re-running migrations is safe

## Step 6: Add Health Check Endpoint

Implement `app/api/health.py`.

Endpoint:

``` text
GET /health
```

Response when healthy:

``` json
{
  "status": "ok",
  "database": "ok"
}
```

Response when unhealthy:

``` json
{
  "status": "error",
  "database": "error"
}
```

Return an HTTP 200 for healthy and HTTP 503 for unhealthy.

Security considerations:

-   Do not expose database URLs
-   Do not expose stack traces
-   Do not expose provider names, API keys, or host internals
-   Keep `/health` unauthenticated for hosting-provider checks

Acceptance criteria:

-   Healthy database returns HTTP 200
-   Unavailable database returns HTTP 503
-   Response body contains only coarse service status

## Step 7: Add Structured Logging

Implement `app/core/logging.py`.

Requirements:

-   JSON logs in production
-   Human-readable logs are acceptable locally
-   Include timestamp, level, logger name, and message
-   Include request ID later when middleware is added
-   Do not log secrets or full environment settings

Recommended log events in Phase 1:

-   App startup
-   App shutdown
-   Health check failure
-   Database connectivity failure

Acceptance criteria:

-   Logs are readable locally
-   Production logs are machine-parseable
-   Database errors are logged without credentials

## Step 8: Add Basic Tests

Create `tests/test_health.py`.

Minimum tests:

-   `GET /health` returns HTTP 200 when database check is mocked healthy
-   `GET /health` returns HTTP 503 when database check is mocked unhealthy
-   Health response does not include secrets or database URLs

Test structure:

-   Use FastAPI `TestClient` or `httpx`
-   Mock the database health helper instead of requiring a real database for
    unit tests
-   Add a separate integration test later for real database connectivity

Acceptance criteria:

-   `pytest` passes locally
-   Tests do not require production secrets
-   Tests do not hit paid APIs

## Step 9: Add Local Development Documentation

Update `README.md` or create a local development section.

Include:

-   Python version
-   Dependency installation command
-   Environment setup
-   Database setup
-   Migration command
-   Run command
-   Test command

Example commands:

``` text
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
pytest
```

If using Docker Compose for local PostgreSQL, document:

-   How to start Postgres
-   Database name
-   User
-   Port
-   How to reset local data

## Step 10: Add Deployment Placeholder

Add the minimal deployment configuration for the selected host.

Examples:

-   `Dockerfile`
-   `render.yaml`
-   `fly.toml`
-   Railway service configuration
-   Procfile-style process command

Required runtime command:

``` text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Deployment requirements:

-   Run migrations as a release step or documented manual step
-   Configure `DATABASE_URL` in provider secrets
-   Configure `APP_ENV=production`
-   Enforce HTTPS at the platform/proxy layer

Acceptance criteria:

-   App can boot in a hosted environment
-   `/health` works from the host health checker
-   Logs are visible in the hosting provider console

## Security Checklist for Phase 1

-   `.env` is ignored by git
-   `.env.example` contains no real secrets
-   Production settings do not fall back to local credentials
-   Health check does not expose secrets or internals
-   Database URL is never logged
-   Migrations do not contain credentials
-   Object storage and model API keys are not introduced yet
-   No unauthenticated application functionality exists beyond `/health`

## Phase 1 Completion Criteria

Phase 1 is complete when:

-   The app boots locally with Uvicorn
-   The app boots in the target hosted environment
-   `GET /health` reports database status
-   Migrations run from a clean database
-   Logging is configured
-   Tests for health behavior pass
-   Local setup and deployment steps are documented

## Handoff to Phase 2

Phase 2 should start from this foundation by adding:

-   `users` table
-   `sessions` table
-   Password hashing
-   Login/logout routes
-   Secure session cookies
-   Admin-only user creation command
-   Authentication middleware or dependencies
