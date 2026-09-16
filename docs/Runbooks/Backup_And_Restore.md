# Backup and Restore Runbook

> Historical hosted-mode runbook. For local workspaces, use the backup and
> restore commands documented in [Updating](../Updating.md).

## Purpose

Ensure the production database can be recovered. Backups are not considered
ready until a restore has been tested against a non-production database.

## Minimum Backup Requirements

-   Automated daily backups.
-   Point-in-time recovery if the managed database provider supports it.
-   Minimum 7-day retention for the private MVP.
-   Backup failure alerts.
-   One successful restore test before production launch.

## Backup Scope

The database contains:

-   Users and sessions
-   Source registry and source versions
-   Parsed documents, sections, chunks, citations
-   Embeddings
-   HPD violation rows
-   Retrieval, answer, and rate limit logs

Raw artifacts are stored separately under `ARTIFACT_STORAGE_PATH` or future
object storage. Artifact storage must also be backed up or reproducible from
public source URLs and source version metadata.

## Restore Test Procedure

1.  Create a temporary restore database.
2.  Restore the latest production backup into the temporary database.
3.  Point a shell at the restore database with `DATABASE_URL`.
4.  Confirm the migration revision:

    ``` text
    uv run alembic current
    ```

5.  Confirm ingestion counts:

    ``` text
    uv run python -m app.cli.ingest status
    ```

6.  Confirm embedding coverage:

    ``` text
    uv run python -m app.cli.embeddings status
    ```

7.  Start a temporary app instance against the restored database.
8.  Run read-only smoke tests:

    -   `GET /health`
    -   Login with a test/private user if available
    -   Authenticated `/search`
    -   Authenticated `/answer`

9.  Delete the temporary restore database after validation.

## Restore Record Template

``` text
Date:
Operator:
Backup timestamp:
Restore database:
Alembic revision:
Ingestion status checked:
Embedding status checked:
Smoke tests passed:
Temporary restore deleted:
Notes:
```

## Emergency Restore Notes

-   Prefer restoring to a temporary database first, then validating before
    replacing production.
-   If production data is corrupted, stop write traffic before restore when the
    hosting platform allows it.
-   Record the incident timeline, backup selected, and validation result.
