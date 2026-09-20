# Compatibility and recovery matrix

| Item | Candidate support | Older/newer behavior |
| --- | --- | --- |
| Python | 3.12 | Other minors are rejected/not advertised |
| Workspace format | 1 | Unknown format must be opened by compatible code |
| Corpus schema | 3 | Supported older pairs upgrade with a verified backup; unknown/newer versions fail closed |
| State schema | 2 | Supported migration pairs: `(1,1)`, `(1,2)`, `(2,1)`, `(2,2)`, `(3,1)` → `(3,2)` |
| Canonical bundle | 1 | Unknown version/member type fails before mutation |
| Workspace backup | 1 | Restores only into a nonexistent selected destination |
| HPD connector | `hpd-soda21-v3` | Unfiltered queries include undated records; earlier cache entries are not reused |
| Embeddings | Exact profile ID/model/dimension/preprocessing | Mismatch uses labeled text fallback; no cross-profile comparison |
| OpenAI-compatible endpoints | Packaged model's Responses/embeddings contract after explicit check | Endpoint changes derive a new profile ID; any override change clears approval; no fallback |
| Legacy PostgreSQL | Read-only current legal export | Original database remains unchanged |

The macOS application offers native confirmation before upgrading a supported
older workspace. It verifies a backup first and restores the previous databases
on an ordinary migration failure. If automatic recovery fails, it offers a
separate recovery copy. See [Updating](Updating.md).

Before a CLI upgrade: stop writers, inspect `git status`, run backup, retain the old
code reference, then locked-sync and run `nyc-housing migrate preflight` followed
by `nyc-housing migrate apply` when available, then `nyc-housing doctor`.
The schema preflight is read-only and fails closed when
the workspace is newer or has no implemented migration path. If new code fails,
return to the compatible code version and original workspace, or restore the
backup into a new path. Never replace a workspace with an older backup merely to
reset usage accounting.
