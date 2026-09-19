# Updating the application and corpus

Application releases and corpus generations are independent. Updating code does
not automatically download legal sources or buy new embeddings.

## Check for a release

There is no verified repository remote in this checkout yet, so no URL is baked
into the application. After the maintainer establishes the public repository:

```bash
uv run nyc-housing update-check \
  --repository https://github.com/OWNER/REPOSITORY --json
```

This reads GitHub release metadata only. It never downloads or executes release
code and never changes the checkout.

## Safe tagged-release workflow

1. Stop `nyc-housing serve` and allow jobs to finish or pause.
2. Run `git status --short`. Do not overwrite a dirty contributor checkout.
3. Create and verify a secret-free workspace backup.
4. Fetch release metadata and inspect release/migration notes.
5. Check out the selected signed/tagged release through your normal Git workflow.
6. Run `uv sync --locked --extra credentials`.
7. Run `nyc-housing migrate preflight` against the workspace. Preflight is
   read-only. If it reports `migration_available`, run
   `nyc-housing migrate apply`; the command creates a workspace backup before
   changing the schema. Then run `nyc-housing doctor` before serving. Stop when
   preflight reports that no reviewed migration path is available.
8. Start, verify status/source/search, and retain the old code reference and backup
   until the new version is confirmed.

Code downgrades do not downgrade local schemas. If an older release does not
support the workspace schema, restore a matching backup into a new destination or
return to compatible code; never manually stamp or rewrite schema versions.

## Update public legal sources

```bash
uv run nyc-housing corpus update
uv run nyc-housing corpus verify
uv run nyc-housing sources --json
```

The update is staged and validated before one active-generation pointer changes.
Eligible single-file publishers receive `If-None-Match` and/or
`If-Modified-Since` on later checks; a valid `304 Not Modified` reuses the
verified local artifact and advances only `last_checked_at`. The multi-page HPD
guidance source is fetched in full because one validator cannot safely describe
all of its pages. Semantic unchanged content is reused even when raw publisher
bytes differ. New/changed chunks do not cause paid embedding work until you run
an estimate and approve indexing. Roll back to the most recent retained
generation with `nyc-housing corpus rollback`.

The browser Sources view provides the same install/update, explicit per-module
partial activation, semantic-index estimate/approval, verification, rollback,
status, cancellation, and resumable-job controls.

Source status flags a module not successfully checked for 30 days. The app cannot
check while it is closed; automatic OS scheduling is an optional future feature.
