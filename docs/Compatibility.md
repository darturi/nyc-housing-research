# Compatibility and recovery matrix

| Item | Candidate support | Older/newer behavior |
| --- | --- | --- |
| Python | 3.12 | Other minors are rejected/not advertised |
| Workspace format | 1 | Unknown format must be opened by compatible code |
| Corpus schema | 1 | Unknown/newer version fails startup; no automatic downgrade |
| State schema | 1 | Must match corpus schema pair; no partial compatibility claim |
| Canonical bundle | 1 | Unknown version/member type fails before mutation |
| Workspace backup | 1 | Restores only into a nonexistent selected destination |
| HPD connector | `hpd-soda21-v2` | Connector version participates in cache identity; v2 adds date-filter and fetch-interval response fields |
| Embeddings | Exact profile ID/model/dimension/preprocessing | Mismatch uses labeled text fallback; no cross-profile comparison |
| OpenAI-compatible endpoints | Packaged model's Responses/embeddings contract after explicit check | Endpoint changes derive a new profile ID; any override change clears approval; no fallback |
| Legacy PostgreSQL | Read-only current legal export | Original database remains unchanged |

Before an upgrade: stop writers, inspect `git status`, run backup, retain the old
code reference, then locked-sync and run `nyc-housing migrate preflight` followed
by `nyc-housing doctor`. The schema preflight is read-only and fails closed when
the workspace is newer or has no implemented migration path. If new code fails,
return to the compatible code version and original workspace, or restore the
backup into a new path. Never replace a workspace with an older backup merely to
reset usage accounting.
