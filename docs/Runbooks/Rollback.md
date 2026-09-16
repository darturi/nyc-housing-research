# Rollback Runbook

> Historical hosted-mode runbook. Local corpus rollback and workspace restore are
> documented in [Updating](../Updating.md).

## Purpose

Provide clear steps for reverting a problematic release while protecting the
database and source traceability.

## First Decision

Before rollback, identify whether the release changed:

-   Application code only.
-   Application code plus database schema.
-   Ingested source data.
-   Production secrets or provider configuration.

Prefer a forward fix for database issues unless a downgrade has been tested and
data loss is acceptable.

## Application Rollback

1.  Identify the previous known-good app version.
2.  Confirm whether the current version ran migrations.
3.  Redeploy the previous app artifact or image.
4.  Keep the database at the current revision unless the previous app cannot
    run against it.
5.  Run smoke tests:

    -   `GET /health`
    -   Login
    -   `/search`
    -   `/answer`

6.  Record the rollback reason, operator, app versions, and validation result.

## Database Rollback

Use database rollback only when necessary.

Options:

-   Apply a tested Alembic downgrade if the migration is reversible and data
    loss is acceptable.
-   Restore from backup into a temporary database and validate before replacing
    production.
-   Write a forward migration or data repair script when safer than downgrade.

Before database rollback:

-   Stop write traffic if possible.
-   Confirm latest backup timestamp.
-   Export or snapshot current production state if the provider supports it.
-   Document expected data loss or schema changes.

Validation commands:

``` text
uv run alembic current
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
```

## Ingestion Rollback

If an ingestion run produced bad source data:

1.  Stop additional ingestion jobs.
2.  Identify the affected `source_versions`, documents, chunks, and embeddings.
3.  Prefer marking or superseding bad source versions over deleting audit
    history.
4.  Regenerate embeddings after correction.
5.  Smoke test `/search` and `/answer` for affected sources.

## Secret Rollback

If a secret was exposed or misconfigured:

1.  Rotate the secret in the provider system.
2.  Update production environment configuration.
3.  Restart affected app processes.
4.  Confirm health check and smoke tests.
5.  Audit logs for accidental secret exposure.

## Rollback Record Template

``` text
Date:
Operator:
Incident:
Previous app version:
Rolled back app version:
Database revision before:
Database revision after:
Backup timestamp:
Smoke tests passed:
Follow-up required:
Notes:
```
