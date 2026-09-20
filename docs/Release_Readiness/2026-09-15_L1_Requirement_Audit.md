# L1 requirement audit — 2026-09-15

Historical snapshot. For current schemas, licensing, repository status, upgrade
behavior, and outstanding gates, see [20 September readiness](2026-09-20_Beta_Batch.md).
The dated observations below are retained for traceability.

Status: **implementation-complete candidate with explicit external gates**.

This audit traces the L1 feature specification and implementation work packages
to executable code, tests, documentation, and remaining evidence. It does not
turn an unrun external gate into a pass. Optional L2 work (bulk HPD, additional
sources, Docker, saved history, and a fully local model) remains out of scope.

## Feature-level disposition

| Feature | Disposition | Implemented evidence | Still required before an L1/open-source release |
| --- | --- | --- | --- |
| F01 installation/startup | Implemented locally; platform gate open | Python 3.12 pin, locked core install, console entry point, package data, OS data paths, idempotent setup, interactive free core-install and optional no-charge model-configuration offers, explicit noninteractive flags, loopback serve, no-browser launch code, Unicode/spaced-path wheel smoke | Run the documented clone and browser journey in clean Linux, macOS, and Windows environments before advertising them |
| F02 credentials/profiles | Implemented; real-provider evidence open | Interactive and explicit hidden-input setup selects packaged OpenAI profiles; environment/keyring/explicit owner-file resolution, write-only UI/API, redacted status, one default key or independent advanced slots, endpoint-derived identities, HTTPS/loopback validation, explicit pricing/privacy metadata, mandatory no-fallback compatibility checks | Explicitly approved real credential validation/answer evaluation; recheck profile/pricing metadata at release |
| F03 local persistence | Implemented for schema v1 | Separate corpus/state SQLite stores, FK/WAL/busy timeout, immutable generations, coordinated writers, checked float32 vectors, active/retained counts, consistent multi-store backup/restore, refusal of unknown schema versions, read-only `migrate preflight` | A future schema bump must add and test a concrete backup-gated migration before changing version 1; none is silently inferred now |
| F04 legal corpus | Implemented; redistribution decision open | Five versioned manifests/parsers, official on-machine acquisition, conditional ETag/Last-Modified refresh for eligible single-file sources, manual artifact import, content-addressed artifacts, source/check/effective-date separation, staged validation/activation, partial/text-only choice, unchanged reuse, disk-publication failure preservation, verify/freeze/activate/rollback/prune, canonical bundle validation | Source-by-source redistribution decisions; keep downloaded corpus bytes out of publication meanwhile |
| F05 local retrieval | Implemented; parity gate open | Generation-scoped FTS5/BM25 rank fusion, normalized exact citations, pre-rank filters, one profile-aware query vector, float32 cosine index, deterministic exact/keyword/vector merge, labeled semantic fallback, 55-case fixture gate, 10k benchmark | Run the frozen, controlled PostgreSQL comparator and require Recall@5/MRR delta no worse than 0.02 |
| F06 grounded answers/evidence | Implemented technically; substantive gate open | Memory-only answer jobs, prompt/source separation, validated evidence markers, provider-failure evidence retention and explicit retry, truncated-response rejection, personal-outcome guard, historical-date guard, prompt version, evidence publisher/retrieval/check/effective dates, combined property/legal evidence, 26-case evaluator | Approved released-profile run and housing-law reviewer sign-off on must-pass claims, qualifications, and refusals |
| F07 live HPD/cache | Implemented as connector v2 | Typed IDs/address/ZIP/class/status/date filters, punctuation and alias validation, candidate selection, bounded projected SODA2.1 calls, deterministic cursor, nullable total, top-level `has_more`/`next_cursor`, process-wide outbound cap, retries/deadline, original statuses, fetch interval/optional source date, 24h/1h freshness, 30-day stale/LRU cache, refresh/paging/source link/scoped exports | Re-run the bounded live contract for the final artifact if release timing/source schema changes; full citywide/offline data remains L2 |
| F09 browser experience | Implemented; remote matrix open | Research/Sources/Settings views; Auto/Search/Answer/Property modes; conservative routing; evidence inspection; full property filters; accessibility labels/table caption/safe text and links; job progress/cancel/resume/retry; spend/cache/offline controls; exports; first-run source/model/credential flow in a real headless-Chrome fixture journey | Execute the checked-in Playwright job on the GitHub runner and complete an independent operator journey on every advertised platform |
| F10 usage/budgets/deadlines | Implemented | Append-only reserve/settle/uncertain/correction ledger, price snapshots, exact local caps, cross-process paid capacity, bulk estimates/ceilings, deadline/cancellation propagation, no automatic paid indexing/evaluation, and explicit one-off unknown-cost admission labeled outside USD caps with separate ledger counts | Recheck packaged prices at release and preserve the explicit paid-evaluation approval gate |
| F11 local access/privacy | Implemented | Loopback-only native bind, one-use expiring fragment/code exchange, process-restart invalidation, HttpOnly/SameSite cookie, Host/Origin/CSRF/session gates, request-size/CSP/no-store/referrer/frame controls, no CORS, offline egress policy, memory-only transcripts, redacted logs/backups and owner-only diagnostic export | Independent hostile-origin/browser run remains part of the clean-machine gate |
| F12 jobs/diagnostics/exports | Implemented | Durable job state/lease/checkpoint/recovery, real corpus update, 30-day source reminders, manual read-only release check, status/sources/doctor/verify and redacted diagnostic export, retention barriers, Markdown/JSON/CSV exports with prompt/profile/source/fetch provenance and formula neutralization, consistent backup/validated new-destination restore | Verify the final release URL/update notes after a remote exists; future scheduled closed-app checks remain L2 |
| F13 legacy migration | Implemented for fixtures; real environment gate open | Poisoned-environment isolation, explicit legacy entry, read-only legal export, exclusion of HPD/users/sessions/logs/secrets, portable canonical import, hash/path/size/vector-profile validation, no mutation of the legacy backend | Run against a controlled read-only copy of the assessed PostgreSQL/artifact environment when authorized; direct S3 fetching is not claimed |
| F14 docs/CI/distribution | Prepared; decision/remote gates open | Canonical local README and linked setup/coverage/privacy/cost/update/recovery/contribution docs, historical hosted runbooks, advanced-only `.env.example`, source/dependency inventory, three-OS native CI, separate Chromium E2E and 10k benchmark jobs, checked-in wheel/sdist content verifier | Add a maintainer-selected code `LICENSE`, finish notices, establish/verify the GitHub destination, run remote CI, approve publication |

F08 and F15–F17 are L2. Their absence does not reduce the five-source legal
corpus; it limits citywide/offline property analytics, additional subject-matter
coverage, deployment variants, persistence choices, and local-model operation as
already documented in Coverage.

## Work-package disposition

| Package | Result | Evidence/gate |
| --- | --- | --- |
| A01 | Implemented | Dirty worktree preserved; poisoned legacy environment and isolated network/workspace tests; baseline artifacts and comparator requirements recorded |
| A02 | Implemented | Explicit `WorkspaceContext`, separate local/legacy configuration, factory construction, two-workspace tests, shared offline policy |
| A03 | Implemented locally | Locked package/entry point/assets, setup/serve, synthetic demo, wheel/sdist and unrelated-directory smoke; native remote runs remain open |
| B01 | Implemented | Separate versioned stores, FTS tables, vector representation, WAL/FK/busy controls, usage/job/cache schema |
| B02 | Implemented | Durable lifecycle, target conflicts, leases/checkpoints, resume/cancel, maintenance barrier, lost-interactive resubmit state |
| B03 | Implemented/live-verified | Five-source download/parse/validate/activate/update/rollback/manual-import flow; unchanged reuse and failure preservation |
| B04 | Implemented in fixtures | Canonical bundles and explicit read-only legacy export/import; real legacy environment run remains controlled/external |
| B05 | Implemented; comparator open | Production FTS5/exact/vector retrieval, deterministic evaluation and capacity gate; PostgreSQL parity evidence remains external |
| C01 | Implemented | Local-session/rebinding/origin/CSRF/security-header and restart tests |
| C02 | Implemented | Secure credential stores/status and packaged/custom profiles with compatibility gate |
| C03 | Implemented | One gateway and ledger for model operations, budgets, concurrency, uncertainty, deadlines, automatic unknown-price blocking, and separately counted one-off unknown-cost approvals |
| C04 | Implemented technically | Reusable embeddings, evidence-first jobs, grounded prompts/citations/refusals, answer evaluator; paid/domain review open |
| C05 | Implemented | Full research/settings/sources flow plus real local Chromium journey |
| D01 | Implemented/live-verified | HPD SODA2.1 typed connector v2, identity/candidates/filters/pagination/errors/concurrency |
| D02 | Implemented | Fetch/source provenance, cache policies, stale/offline semantics, pins, refresh, bounded durable export |
| D03 | Implemented | Conservative routing, explicit mode, candidate selection, evidence table, paging, scoped export, combined summary |
| E01 | Implemented | Diagnostics, preflight, recovery, backup/restore, retention, update check and provenance-complete exports |
| E02 | Locally evidenced; external gates open | Unit/fault/security/privacy/package/browser/benchmark/live-source evidence exists; remote platform, parity, paid, legal, and independent-operator evidence does not |
| E03 | Prepared, not publish-authorized | Documentation, manifests, compatibility, release notes, audits, and an enforced release-content verifier exist; license/remote/notices/publication remain maintainer actions |

## Current repeatable commands

```bash
uv sync --locked --extra dev
uv run ruff check .
uv run pytest -q
NYC_HOUSING_BROWSER_E2E=1 uv run pytest tests/e2e/test_local_browser.py -q
uv run nyc-housing benchmark --chunks 10000 --dimension 1536 --runs 20 --json
uv lock --check
uv build --no-sources
uv run python -m app.maintenance.release_audit dist/*.whl dist/*.tar.gz
```

The browser command additionally requires a Playwright Chromium installation;
CI installs it explicitly. Ordinary tests skip that one test and make no browser,
live-source, legacy-database, or paid-provider request.

## Release decision

Do not claim L1/open-source completion yet. The implementation no longer needs a
maintainer-hosted service, and the in-repository L1 product surface is operational,
but the license, remote CI, controlled parity run, approved paid evaluation,
housing-law review, and independent clean-machine journey remain explicit gates.
