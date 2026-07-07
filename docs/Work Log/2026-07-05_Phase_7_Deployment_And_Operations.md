# Work Log: Phase 7 Deployment and Operations

Date: 2026-07-05

## Summary

Implemented Phase 7 of the NYC Housing Law RAG MVP based on
`docs/MVP Implementation Breakdown/Phase_7_Deployment_And_Operations.md`.

This phase added production operations documentation: deployment, backup and
restore, ingestion and maintenance, monitoring and error logging, rollback, and
README links to those runbooks.

## Files Added

-   `docs/Runbooks/Deployment.md`
-   `docs/Runbooks/Backup_And_Restore.md`
-   `docs/Runbooks/Ingestion.md`
-   `docs/Runbooks/Monitoring.md`
-   `docs/Runbooks/Rollback.md`
-   `docs/Work Log/2026-07-05_Phase_7_Deployment_And_Operations.md`

## Files Updated

-   `README.md`

## Implementation Details

### Deployment

Added a deployment runbook with production settings, HTTPS requirements,
migration procedure, startup command, smoke tests, production user setup, and a
deployment record template.

### Backup and Restore

Added a backup and restore runbook requiring automated backups and one tested
restore before launch. The restore procedure validates Alembic revision,
ingestion status, embedding status, and read-only app behavior.

### Ingestion and Maintenance

Added an ingestion runbook for full MVP ingestion, single-source ingestion, HPD
violation loading, embedding generation, post-ingestion smoke tests, and rate
limit event pruning.

### Monitoring and Error Logging

Added monitoring guidance for `/health`, crash loops, database failures, high
`5xx` rates, backups, provider failures, and operational database checks. The
logging section specifies data that must be scrubbed or avoided.

### Rollback

Added rollback procedures for application releases, database schema changes,
bad ingestion runs, and secret misconfiguration.

## Verification

Commands to run after this documentation phase:

``` text
uv run --extra dev pytest
uv run --extra dev ruff check .
uv run alembic heads
uv run alembic current
uv run python -m app.cli.ingest status
uv run python -m app.cli.embeddings status
uv run python -m app.cli.limits prune-events
```

## Known Notes

-   Phase 7 added operational documentation only. No runtime behavior changed.
-   Production backup configuration, uptime checks, and restore testing must be
    completed in the actual hosting environment.

## Next Step

Before launch, follow the deployment and backup runbooks against the intended
hosting provider, then record the deployment and restore-test results.
