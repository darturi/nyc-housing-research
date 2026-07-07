# Phase 7: Deployment and Operations Implementation

## Goal

Prepare the private MVP for a real hosted deployment. Phase 7 should make the
application operable outside local development by defining production
configuration, HTTPS-only access, secret handling, migrations, backups,
restore testing, object storage retention, uptime/error monitoring, ingestion
operations, and rollback procedures.

This phase should produce deployable operational artifacts and runbooks. It
should not add public signup, billing, a full admin dashboard, multi-region
high availability, Redis, OpenSearch, a graph database, or case law retrieval.

## Scope

### Included

-   Production environment configuration checklist
-   HTTPS-only deployment requirements
-   Secure session cookie production defaults
-   Secret-management runbook
-   Database migration deployment procedure
-   Database backup and restore test runbook
-   Object storage lifecycle and privacy policy
-   Basic uptime monitoring plan
-   Error logging and operational log guidance
-   Manual ingestion runbook
-   Rate limit event pruning operation
-   Rollback instructions for app and database changes
-   Production smoke test checklist
-   README updates
-   Work log entry after implementation

### Explicitly Deferred

-   Public user registration
-   Multi-tenant billing
-   Automated admin dashboard
-   Multi-region deployment
-   Blue/green deployment automation
-   Redis-backed rate limiting
-   OpenTelemetry tracing stack
-   Full incident management system
-   Automated source ingestion scheduler beyond a documented host job

## Data and Code Dependencies

Phase 7 assumes Phases 1 through 6 have already delivered:

-   FastAPI app and health check
-   Database migrations through Phase 6
-   Private user authentication
-   Source registry and ingestion CLI
-   Embedding CLI
-   Retrieval and answer endpoints
-   `retrieval_logs`, `answer_logs`, and `rate_limit_events`
-   Rate limiting and abuse controls
-   `.env.example`
-   Dockerfile or host-compatible app start command

The deployment should use only freely accessible public source data. Paid
services may provide hosting, managed database, object storage, monitoring,
embedding, reranking, or answer generation, but not corpus data.

## Deliverables

-   Production deployment checklist
-   Secret-management checklist
-   Migration and release procedure
-   Backup and restore runbook
-   Object storage lifecycle notes
-   Ingestion runbook
-   Rollback runbook
-   Smoke test checklist
-   Monitoring and error logging guidance
-   README deployment updates
-   Optional `docs/Runbooks/` directory for operational documents
-   Work log entry after implementation

## Design Principles

### Operations Before Features

The deployed MVP should be boring to operate. Prefer clear manual commands,
checklists, and reversible steps over adding new infrastructure before there is
real usage.

### Secure by Default in Production

Production settings should enforce HTTPS assumptions, secure cookies, server
side secrets, private object storage, database backups, and authenticated
access for every non-health endpoint.

### Restore Is Part of Backup

Backups are not complete until a restore has been tested at least once against
a non-production database.

### Runbooks Are Product Surface

Manual ingestion, rollback, and recovery steps should be documented clearly
enough that they can be followed during a production incident without reading
application source code.

### Preserve Source Traceability

Operations should not weaken the corpus controls from earlier phases. Raw
artifacts, parsed chunks, source URLs, source versions, and access notes must
remain available for auditability.

## Suggested Files

``` text
docs/
├── Runbooks/
│   ├── Deployment.md
│   ├── Backup_And_Restore.md
│   ├── Ingestion.md
│   ├── Monitoring.md
│   └── Rollback.md
└── Work Log/
    └── 2026-07-05_Phase_7_Deployment_And_Operations.md
```

If the project should stay lighter, these runbooks can be sections in
`README.md`, but separate files are preferable once production procedures are
more than a few paragraphs.

## Production Configuration

Required production environment variables:

``` text
APP_ENV=production
DATABASE_URL=...
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=lax
RATE_LIMIT_ENABLED=true
ARTIFACT_STORAGE_BACKEND=local
ARTIFACT_STORAGE_PATH=...
ANSWER_LLM_PROVIDER=fake
```

If enabling paid providers later:

``` text
EMBEDDING_API_KEY=...
ANSWER_LLM_API_KEY=...
```

Production checks:

-   `SESSION_COOKIE_SECURE=true`
-   App is served only through HTTPS
-   Database URL is stored in the hosting provider secret manager
-   Provider API keys are server-side only
-   `.env` is not committed
-   Object storage or artifact path is private
-   Backups are enabled before production traffic
-   Rate limits are enabled
-   `/health` is reachable without auth
-   `/search` and `/answer` require auth

## Deployment Procedure

Recommended release flow:

1.  Build the app image or deploy artifact.
2.  Set production environment variables through the hosting provider.
3.  Run database migrations:

    ``` text
    uv run alembic upgrade head
    ```

4.  Start the app:

    ``` text
    uvicorn app.main:app --host 0.0.0.0 --port $PORT
    ```

5.  Run production smoke tests.
6.  Confirm monitoring is green.
7.  Record deployment version, migration head, and timestamp in the work log or
    deployment notes.

For hosts that support release commands, run migrations as a release step
before web traffic is shifted to the new version.

## Smoke Test Checklist

After deployment:

-   `GET /health` returns `200`.
-   Anonymous `POST /search` returns `401`.
-   Anonymous `POST /answer` returns `401`.
-   Admin user can log in.
-   Authenticated `/auth/me` returns the current user.
-   Authenticated `/search` returns results after ingestion and embeddings.
-   Authenticated `/answer` returns a disclaimer and source-backed citations.
-   Repeated failed login attempts eventually return `429`.
-   Oversized request returns `413`.
-   `uv run alembic current` reports the expected head.

## Secret Management

Secrets should live in the hosting provider's secret manager or encrypted
environment variable system.

Secrets:

-   `DATABASE_URL`
-   Managed database credentials
-   Object storage credentials, if used
-   Embedding provider API key, if used
-   Answer provider API key, if used
-   Monitoring/error logging DSN, if used

Rules:

-   Never commit `.env`.
-   Use separate development, staging, and production credentials.
-   Rotate provider keys before wider testing.
-   Do not expose secrets through logs, health checks, API responses, or client
    JavaScript.
-   Keep a manual record of where each production secret is configured, not the
    secret value itself.

## Database Backups

Minimum production requirement:

-   Daily automated backups
-   Point-in-time recovery if the managed provider supports it
-   Retention window of at least 7 days for the private MVP
-   One tested restore before launch

Restore test:

1.  Create a temporary restore database.
2.  Restore the latest backup into it.
3.  Run:

    ``` text
    uv run alembic current
    ```

4.  Confirm core table counts:

    ``` text
    uv run python -m app.cli.ingest status
    uv run python -m app.cli.embeddings status
    ```

5.  Run a read-only smoke test against the restored database.
6.  Delete the temporary restore database after validation.

Document the restore date, backup timestamp, restored migration revision, and
validation result.

## Object Storage and Artifacts

The MVP currently supports local artifact storage. For production, either mount
persistent private storage or move to an S3-compatible private bucket in a
future phase.

Requirements:

-   Raw artifacts must not be publicly served.
-   Artifact paths or bucket objects should be backed up or reproducible from
    public source URLs.
-   Lifecycle policy should retain raw source artifacts long enough to audit
    source versions.
-   Parsed database records must remain traceable to source URLs and source
    version hashes.

Suggested lifecycle policy:

-   Keep current raw artifacts indefinitely while the source version is current.
-   Keep superseded artifacts at least 90 days.
-   Do not delete artifacts required by any current `source_versions` row.

## Monitoring

Minimum monitoring:

-   Uptime check for `GET /health`
-   Alert on repeated health check failures
-   Alert on application crash/restart loops
-   Alert on database connection failures
-   Alert on high `5xx` rate
-   Alert on backup failure
-   Track answer provider failures if a paid provider is enabled

Useful operational checks:

-   `retrieval_logs` volume by day
-   `answer_logs.answer_status` distribution
-   `rate_limit_events` volume by event type
-   Latest successful ingestion run per source
-   Embedding coverage from `app.cli.embeddings status`

## Error Logging

The app already uses structured logging. Production logging should:

-   Capture app startup and shutdown
-   Capture uncaught exceptions
-   Capture provider failures without API keys or full prompts
-   Avoid raw passwords, session tokens, cookies, or secret values
-   Avoid logging full user questions if they may contain sensitive housing
    facts, or make query logging retention configurable before wider launch

If adding a hosted error logging tool, configure its DSN as a server-side
secret and scrub:

-   `Authorization`
-   `Cookie`
-   Session cookie name
-   API keys
-   Raw request bodies

## Manual Ingestion Runbook

Before ingestion:

1.  Confirm production database backup exists.
2.  Confirm target source is freely accessible and still allowed by the source
    registry notes.
3.  Confirm artifact storage has enough space.

Run registry and ingestion:

``` text
uv run python -m app.cli.ingest seed-sources
uv run python -m app.cli.ingest ingest-mvp
```

For a single legal source:

``` text
uv run python -m app.cli.ingest ingest-source nyc-housing-maintenance-code
```

After ingestion:

``` text
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings generate
uv run python -m app.cli.embeddings status
```

Then manually test authenticated `/search` and `/answer`.

## Maintenance Runbook

Routine commands:

``` text
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
uv run python -m app.cli.limits prune-events
uv run alembic current
```

Suggested cadence:

-   Daily: uptime and backup status
-   Weekly: ingestion status, embedding coverage, rate limit event volume
-   Monthly: restore test, secret access review, dependency update review

## Rollback Runbook

Application rollback:

1.  Identify the previous known-good app release.
2.  Confirm whether the current release ran a database migration.
3.  If no irreversible migration was run, redeploy the previous app release.
4.  Run smoke tests.
5.  Record the rollback reason and resulting app version.

Database rollback:

-   Prefer forward fixes for production database issues.
-   Use Alembic downgrade only if the migration is known reversible and data
    loss is acceptable.
-   If data was corrupted, restore from backup into a temporary database first
    and validate before replacing production.

Rollback checks:

``` text
uv run alembic current
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
```

If rollback affects answer generation or rate limits, verify `/answer`,
`answer_logs`, and `rate_limit_events` behavior explicitly.

## Testing Plan

### Documentation Checks

-   README contains production startup and migration commands.
-   Runbooks list required environment variables.
-   Backup runbook includes a restore test.
-   Ingestion runbook includes embedding regeneration.
-   Rollback runbook distinguishes app rollback from database rollback.

### Production-Like Smoke Tests

-   Start app with `APP_ENV=production`.
-   Confirm secure-cookie configuration is documented and enabled.
-   Run migrations against a clean database.
-   Create an admin user.
-   Login, search, answer, logout.
-   Confirm unauthenticated search and answer return `401`.

### Operational Tests

-   Confirm `uv run python -m app.cli.ingest status` works.
-   Confirm `uv run python -m app.cli.embeddings status` works.
-   Confirm `uv run python -m app.cli.limits prune-events` works.
-   Confirm backup restore test has been performed once before launch.

## Acceptance Criteria

-   Production app is reachable only over HTTPS.
-   Production session cookies are secure.
-   Secrets are not committed to the repository.
-   Database backups are enabled.
-   A restore has been tested once.
-   Object storage or artifact storage is private and covered by lifecycle
    guidance.
-   Uptime monitoring is configured for `/health`.
-   Error logging avoids secrets and sensitive request bodies.
-   Manual ingestion, maintenance, and rollback runbooks exist.
-   Production smoke test checklist exists and has been run once.

## Implementation Order

1.  Add runbook directory and deployment runbook.
2.  Add backup and restore runbook.
3.  Add ingestion and maintenance runbook.
4.  Add monitoring and error logging runbook.
5.  Add rollback runbook.
6.  Update README deployment section with links to runbooks.
7.  Add any missing production environment variables to `.env.example`.
8.  Verify existing CLI commands still run.
9.  Run lint and tests.
10. Add Phase 7 work log after implementation.

## Manual Verification

After implementation, run:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic current
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
uv run python -m app.cli.limits prune-events
```

Then follow the smoke test checklist against the intended hosting
environment.

## Next Step

After Phase 7, the MVP has the core private deployment foundation. Future
phases should be driven by real usage: production LLM provider integration,
better ingestion scheduling, Redis-backed rate limits, richer property search,
OpenSearch, case law ingestion from permitted public sources, or a small admin
operations UI.
