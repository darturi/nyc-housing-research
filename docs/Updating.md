# Updating the application and corpus

Application releases and corpus generations are independent. Updating code does
not automatically download legal sources or buy new embeddings.

## Check for a release

The configured repository remote is `https://github.com/darturi/NYC_Housing_Rag`.
Select it explicitly for a metadata-only release check:

```bash
uv run nyc-housing update-check \
  --repository https://github.com/darturi/NYC_Housing_Rag --json
```

This reads GitHub release metadata only. It never downloads or executes release
code and never changes the checkout.

## Desktop workspace upgrade

Replace the application with a compatible release and launch it. When a supported
older workspace is detected, the app explains the upgrade and asks for native
confirmation. Cancel leaves the schema unchanged. Confirm creates and verifies a
unique pre-migration ZIP under the workspace's `backups` folder before upgrading.
No provider calls or source downloads are part of the migration.

Keep the application open until the upgrade finishes. It holds the launch lock
through migration and shutdown, so closing the window does not release ownership
while the databases are being changed. The current pair is corpus **3**, state
**2**; unknown or newer schemas remain blocked.

An ordinary migration error restores exact local snapshots of both pre-upgrade
databases and reports the durable backup path. Before changing either database,
the app also saves a recovery journal and verified snapshots in the workspace.
If the process stops unexpectedly, the next desktop launch offers recovery before
opening the app. An unfinished upgrade restores **both** previous databases;
an upgrade that already committed keeps the upgraded databases and finishes cleanup.
Recovery can itself be interrupted and safely repeated. A recovered older schema
still needs a successful upgrade before this release can open it.

If those snapshots cannot be restored, the desktop offers **Restore and open a
recovery copy**. It verifies the backup, restores into a separate sibling folder
named `…-recovered-…`, upgrades that copy, and opens it. The original workspace
and backup remain intact. Only after the copy upgrades successfully does the app
remember it for future desktop launches. Credentials are excluded from backups
and may need to be configured again. CLI commands still use their explicit or
default workspace path; use `--data-dir /path/to/recovered-workspace` to work on
the same recovery copy. The desktop selection is recorded in
`desktop-recovery.json` in the original workspace. A missing selected folder
stops startup instead of silently switching back to older data.

CLI recovery is explicit and runs under the same workspace locks:

```bash
uv run nyc-housing migrate preflight --json
uv run nyc-housing migrate recover --json
uv run nyc-housing migrate preflight --json
# If the recovered workspace reports migration_available:
uv run nyc-housing migrate apply --json
```

Preflight reports `recovery_required` without opening either database when a
journal exists. `migrate apply` also recovers an interrupted attempt before
retrying; `start` recovers before checking compatibility. Other workspace database
operations stop until recovery finishes. Do not delete the journal or manually
change schema versions. If the journal itself is damaged, preserve the workspace
and restore its pre-migration ZIP to a new directory using the commands in
[Troubleshooting](Troubleshooting.md#backup-and-restore).

The journal protects upgrades started by this implementation. Older interrupted
upgrades without a journal still require a verified backup restore. Automated
checks terminate real subprocesses at migration and recovery boundaries; physical
power-loss behavior and native dialogs still require platform release testing.

## Backup size and storage

Workspace backup creation, validation, and restoration stream file contents in
1 MiB chunks. Existing format-1 backups remain supported. The existing 4 GiB
compressed/expanded archive limit remains; manifests/settings are capped at
16 MiB each and archives at 50,000 members. Member hashes, sizes, paths, and
artifact mappings are checked before a restored workspace becomes visible.

Allow disk space for the archive, SQLite snapshot copies, and a staged restore.
Streaming limits content held in memory; it does not eliminate temporary disk
use. Upgrade snapshots are private, exact local database copies, including local
session state. They are deleted after a completed upgrade or recovery. An abrupt
exit during preparation or final cleanup can leave private `.migration-staging-*`
or `.migration-complete-*` folders; these are not active recovery journals. The
portable ZIP remains sanitized and excludes credentials and session tokens.

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
