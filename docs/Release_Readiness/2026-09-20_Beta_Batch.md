# Beta implementation batch — 20 September 2026

Status: **implemented locally; external release evidence remains open**.

Follow-up: [streaming backups and interrupted-upgrade recovery](2026-09-20_Recovery_Batch.md)
supersedes this snapshot's backup-memory and crash/recovery-copy limitations.
The validation results below describe the first batch.

This record supersedes the compatibility and release-status statements in the
14–15 September snapshots. Those reports remain historical evidence rather than
instructions for the current checkout.

## Current compatibility and release facts

- Application version remains the unreleased `0.1.0` candidate; Python 3.12.
- Workspace format 1; corpus schema 3; state schema 2; backup/bundle formats 1.
- HPD connector `hpd-soda21-v3` includes undated rows in unfiltered lookups.
- The repository's original code/documentation is Apache-2.0. Dependency notices
  and rights to redistribute downloaded source material are separate reviews.
- The configured Git remote is `https://github.com/darturi/NYC_Housing_Rag.git`.
  Remote configuration does not establish CI success or publication of an artifact.

## Delivered changes

**Workspace upgrades.** The macOS app offers native confirmation for supported
older workspace pairs. Cancellation leaves the schema unchanged. An accepted
upgrade creates and verifies a unique backup before changing either database.
Ordinary migration failures restore exact local pre-upgrade database snapshots;
if recovery also fails, the UI offers a verified copy in a new sibling directory.
The desktop keeps its workspace lock until startup/migration work has finished,
including after the window closes. The same rollback protection applies to CLI
migrations. Unknown/newer schemas remain blocked.

**Evaluation.** The 55-question regression now runs production ingestion and
local exact/keyword retrieval over independent synthetic documents and distractors.
A missing full-text index fails the gate. One retained generation is used through
a retrieval run. Answer evaluation checks expected citations in evidence actually
cited in prose, stops on a corpus change, and can explicitly save a versioned
report with answers, excerpts, provenance, and profile/price snapshots.

**Human review.** `review-answers` creates pending review templates bound to the
report's ID and hash. Reviewers record accuracy, qualifications/missing facts,
citation support, and absence of unsupported claims separately. Changed reports,
missing/duplicate cases, and incomplete reviewer identity fail validation.
Synthetic, incomplete, or technically failing runs cannot be accepted even when
human dimensions are marked pass. Reviewer identity is self-reported; this is a
review record, not independent certification or overall release approval.

**Documentation.** README, release notes, compatibility, desktop distribution,
update/migration/recovery instructions, privacy documentation, and the answer
review runbook now describe the current implementation and remaining gates.

## Validation

Validation uses isolated temporary workspaces and synthetic/mock providers.
No user workspace is migrated, no credentials are read, and no paid evaluation
or live publisher request is made by this batch's checks.

Results:

- **421 distinct tests passed across runs; 2 PostgreSQL variants skipped.** The
  full sandbox run passed 415 cases; its three socket-restricted launcher tests
  passed with localhost access, as did the opt-in Chrome browser journey. Two
  additional migration fault/shutdown cases passed in the final focused run.
- **28 net additional regression cases** versus the starting suite. The final
  answer/desktop focused run passed 28 cases; the earlier broader focused run
  passed 61 before the final two shutdown/archive cases were added.
- `ruff check --no-cache .` and `git diff --check` passed.
- Offline wheel and source-distribution builds passed the release-content audit.
  The source distribution includes test helpers/fixtures and current upgrade,
  compatibility, review, privacy, release, and readiness documentation.

Focused coverage includes upgrade acceptance/cancellation, unsupported schemas,
backup failure, partial schema changes with rollback/retry, recovery-copy fallback,
real local retrieval and broken-index detection, generation consistency,
actually-cited evidence, explicit report persistence, and report-bound reviews.

## Remaining release evidence and limitations

- Run a bounded, approved real-provider evaluation on a frozen full legal corpus
  and obtain substantive housing-law review. Synthetic CI retrieval is a software
  regression, not a legal-content or semantic-model quality score.
- Tie passing platform CI and clean-machine operator journeys to the final
  release revision. Exercise the native confirmation/recovery dialogs on the
  actual signed/notarized macOS artifact; fake-window tests do not establish this.
- Complete the controlled PostgreSQL retrieval comparator, final dependency
  notices, and publisher/provider contract checks appropriate to the release.
- A process kill/power loss cannot run exception recovery. The durable pre-upgrade
  backup is retained; incompatible interrupted states need verified restoration.
- A recovery copy preserves the old schema, is not automatically selected as the
  desktop's default workspace, and excludes credentials by backup policy.
- History reclamation, streaming large backups, complete localization, source
  expansion, and broad API/CLI decomposition are separate follow-up work.
