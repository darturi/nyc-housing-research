# Streaming backup and recovery batch — 20 September 2026

Status: **implemented locally; platform release validation remains open**.

This follow-up supersedes the backup-memory and interrupted-upgrade limitations
in the [first beta batch](2026-09-20_Beta_Batch.md). Application, workspace,
database, and portable-backup format versions are unchanged.

## Delivered behavior

- Backup creation, full hash verification, and staged restoration stream file
  contents in 1 MiB chunks. Backup format 1 and the 4 GiB size limit remain.
  Manifests/settings and entry counts have explicit bounds; duplicate entries,
  missing required files, unsafe paths, conflicting targets, incomplete artifact
  mappings, and incorrect sizes/hashes prevent publication of a restore.
- Upgrades publish a private, flushed journal and exact database snapshots before
  changing schemas. Normal workspace database access and setup refuse an active
  recovery journal. Preflight reports recovery without opening either store.
- Recovery verifies both snapshots before restoring either store, runs under
  launch/maintenance locks, and can be repeated after another abrupt exit.
  An unfinished upgrade restores both old stores; a committed upgrade retains
  the new stores and finishes cleanup. Finalized journals are retired atomically
  before their snapshots are deleted.
- The desktop offers recovery on its next launch. If local snapshot recovery
  fails, it can restore a verified ZIP into a new sibling folder, upgrade/open
  that copy, and remember the selection for future desktop launches. The original
  and selected workspace remain locked through startup/shutdown. Missing selected
  folders stop startup instead of silently opening older data.
- CLI `migrate recover` performs recovery without starting another upgrade.
  `migrate apply` recovers before retrying, and `start` recovers before checking
  compatibility. CLI workspace selection remains explicit.

## Validation

All checks use temporary workspaces and synthetic fixtures. No existing user
workspace was migrated and no provider or publisher request was needed.

- 446 distinct tests passed across runs; 3 skipped (the opt-in browser journey
  and two PostgreSQL variants). The full sandbox run passed 443 tests; three
  localhost launcher tests passed when rerun with socket access.
- 26 additional test cases cover archive streaming/limits/structure, failed
  writes, abrupt subprocess exit during partial DDL, between stores, before and
  after commit, interruption during recovery and cleanup, damaged snapshots and
  journals, lock ownership, CLI recovery, and desktop recovery/copy selection.
- A 24 MiB artifact round-trip through backup, validation, and restoration stays
  below 16 MiB of traced Python allocations. This is a regression check for
  archive-content allocations, not a claim about total process RSS.
- `ruff check . --no-cache` and `git diff --check` passed.

## Limits and remaining release evidence

- Process-exit tests establish the recovery protocol's behavior at injected
  boundaries. They do not establish hardware power-loss guarantees or replace
  Windows/Linux and signed/notarized macOS release testing. Directory syncing is
  performed on POSIX; Windows uses file flushing and atomic filesystem operations.
- The new journal cannot recover older interrupted upgrades that never created
  one. Preserve those workspaces and restore a verified backup into a new folder.
- Portable backups exclude credentials and local sessions. Exact local recovery
  snapshots can include sessions/cache and must stay private. An abrupt exit
  during preparation or final deletion can leave inactive private staging folders.
- Streaming still requires disk space for SQLite snapshots, archive output, and
  staged restores. It does not raise the portable-backup size limit.
- Real-provider evaluation, substantive housing-law review, platform release
  evidence, complete localization, and safe history reclamation remain separate.
