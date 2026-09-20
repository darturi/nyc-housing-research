# Migrating from the hosted/legacy installation

Migration is explicit and one-way into a new local workspace. It does not modify
the PostgreSQL database or object store, create users, import sessions, or move HPD
bulk rows.

## Export current legal evidence

Install the legacy extra in the checkout that can reach the old database:

```bash
uv sync --locked --extra legacy
export NYC_HOUSING_LEGACY_DATABASE_URL='postgresql+psycopg://...'
uv run nyc-housing migrate export-legacy ./legal-corpus.zip \
  --database-url-env NYC_HOUSING_LEGACY_DATABASE_URL \
  --artifact-root /path/to/staged/legacy-artifacts
```

The database URL stays in the environment, not shell arguments or output. The
exporter starts a PostgreSQL read-only transaction where supported, freezes
current active non-HPD source versions, reads only source/document/chunk/citation
tables, verifies artifact hashes, and emits a canonical data-only bundle.

If legacy artifacts are in S3, copy only the selected source-version objects to a
private local staging root first. Direct S3 export is deliberately not hidden
behind the old ingest/logging services. Keep the output private unless every
source has an affirmative redistribution decision.

Legacy vectors are excluded because the old rows do not prove a complete,
versioned preprocessing profile. Inventing compatibility could corrupt ranking.
After import, keyless text search works; re-embedding is a separate estimated and
approved operation.

## Import into a new local workspace

```bash
uv run nyc-housing --data-dir /new/local/workspace setup
uv run nyc-housing --data-dir /new/local/workspace \
  corpus bundle inspect ./legal-corpus.zip --json
uv run nyc-housing --data-dir /new/local/workspace \
  corpus import ./legal-corpus.zip --allow-partial
uv run nyc-housing --data-dir /new/local/workspace corpus verify
```

Import validates archive paths, member types/sizes/hashes, records, vector shape
when present, references, and indexes before activation. It executes no bundled
SQL or Python. Application state, credentials, users, sessions, questions,
answers, logs, usage events, HPD rows, and private absolute paths are excluded.

The original environment remains authoritative and unchanged until the maintainer
separately decides to retire it. Deleting the old database/bucket or rotating its
credentials is not part of migration.

## Upgrading an existing local workspace

Local schema upgrades are separate from hosted-data migration. The macOS desktop
app offers a confirmed backup-and-upgrade flow before startup; see
[desktop upgrade and recovery](Updating.md#desktop-workspace-upgrade).
For the CLI, check first:

```bash
uv run nyc-housing migrate preflight --json
```

When the result reports `migration_available`, stop the browser app and run:

```bash
uv run nyc-housing migrate apply --json
```

Supported `(corpus, state)` pairs `(1,1)`, `(1,2)`, `(2,1)`, `(2,2)`, and `(3,1)`
upgrade to `(3,2)`. Migration creates and verifies a unique pre-upgrade backup
under `backups`, adds missing user-resource and saved-research tables/fields,
and rebuilds the shared full-text index where required. Ordinary migration
failures restore both previous databases; the backup is retained for recovery.
New upgrades also persist verified local snapshots and a recovery journal before
DDL. If preflight reports `recovery_required`, run `nyc-housing migrate recover`
and preflight again. Recovery either restores both old databases or finishes
cleanup of a committed upgrade; repeated recovery is safe after another process
interruption. `migrate apply` performs that recovery before retrying an upgrade.
Unknown schema versions fail closed. Replacing application code never downgrades
a workspace, and no schema version should be manually stamped to force startup.
