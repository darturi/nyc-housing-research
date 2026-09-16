# NYC Housing RAG: Local Distribution Implementation Plan

Status: proposed execution plan; work packages are not completion claims.

Date: 2026-09-14.

Authority: [Local Distribution Feature Specification](Local_Distribution_Feature_Specification.md).
Feature IDs F01–F17 refer to that specification. Where implementation discovers
a material scope change, record the decision and update both documents before
changing the release contract.

## 1. Delivery strategy

Implement the overhaul incrementally in the existing Python/FastAPI project.
Keep the official-source parsers, citation knowledge, provider abstractions,
and useful tests. Replace the deployment assumptions and storage boundaries;
do not start a separate application or require a frontend-framework rewrite.

The first supported local release, **L1**, includes legal research, user-supplied
model credentials, live/cached property lookup, safe updates, migration, and
cross-platform installation. **L2** contains the optional bulk HPD database,
additional legal modules, container packaging, saved history, and local models.
An internal milestone is not a public L1 release with omitted requirements.

### 1.1 Milestones

| Phase | Work packages | Demonstrable result | Relative effort/risk |
| --- | --- | --- | --- |
| A — Safe foundation | A01–A03 | Isolated tests and a packaged local setup shell | Medium; import/configuration coupling |
| B — Local corpus | B01–B05 | Keyless legal search, validated updates, and legacy import | Large; persistence and retrieval parity |
| C — Keyed research | C01–C05 | Secure, metered, cited answers in the local UI | Large; accounting, cancellation, and quality |
| D — Property research | D01–D03 | Live HPD lookup, cache, pagination, and summaries | Medium–large; external API/identity behavior |
| E — Supported release | E01–E03 | Recovery tools, documentation, platform evidence, release candidate | Large; end-to-end verification |
| F — Optional extensions | X01–X05 | Independently installable advanced capabilities | Separately estimated after L1 |

Effort labels compare work packages; they are not calendar commitments. Estimate
dates after the configuration/migration spikes and live-source contract checks.
Account separately for maintainer decisions, legal review, provider access,
and unavailable platform test machines. Do not count those dependencies as
automatically resolved by finishing code.

### 1.2 Sequencing constraints

- A01's test isolation comes before running destructive fixtures or refactoring
  shared configuration. Preserve the dirty worktree throughout implementation.
- A02's explicit workspace/context comes before introducing new databases or
  exposing local settings. Importing a module must not select a remote backend.
- B01 owns corpus/state schemas; B02 owns job transitions. B03/B04 build on both.
- B05 can use fake or previously captured query vectors. New paid query or
  corpus embeddings wait for C02/C03 credentials and metering.
- C01's local-session controls come before browser endpoints that accept
  credentials, mutate data, export private results, or initiate paid work.
- C03's accounting gateway comes before any real-provider acceptance run.
- D01's connector contract and fixtures can be developed once A02/B01 contracts
  stabilize; integrated summaries wait for C04. No bulk HPD work blocks L1.
- E01 recovery primitives begin with B01; only their complete UI/CLI and recovery
  drills wait until Phase E. CI begins in A01 and expands at each phase.
- Documentation and tests accompany every package. Phase E verifies and finishes
  them; it is not the first time they are written.

### 1.3 Guardrails

1. Default installation never provisions PostgreSQL, S3, Redis, a model proxy,
   or a maintainer-operated service.
2. No automatic adoption of the current `.env`, remote `DATABASE_URL`, S3
   configuration, or paid-provider credentials.
3. All source and query model calls use the same metered execution boundary.
   Offline/fake paths must remain independently testable.
4. Corpus activation is transactional; fetching or parsing is not activation.
   Failed work preserves the last usable generation and all committed spend.
5. Interactive transcripts remain memory-only by default. Persisted jobs do
   not become an accidental query-history feature.
6. No global HPD download is triggered by setup, an address query, or a cache miss.
7. Do not modify the existing remote database, buckets, or applied migration
   history as part of making a new local workspace.
8. Publishing, source redistribution, choosing a code license, and retiring
   existing infrastructure are distinct maintainer decisions, not implicit
   consequences of executing this plan.

## 2. Starting point and implementation boundaries

The feature specification records the assessed baseline: 690 active legal
chunks, 1,198 including historical versions, and approximately 11.1 million
HPD rows occupying 23 GB in PostgreSQL. Preserve that distinction throughout
migration/status work. These are historical measurements to revalidate when
implementation begins, not hardcoded expected counts for future source updates.

Specific coupling visible in the current code:

| Current area | Why it affects the plan |
| --- | --- |
| `app/core/config.py`, `app/main.py`, `app/db/session.py`, `app/web.py` | Cached settings and import-time application/engine construction must be separated from workspace selection |
| `tests/conftest.py` | `DATABASE_URL` uses `setdefault`, while fixtures call `drop_all`; inherited configuration is unsafe |
| `app/db/migrations/env.py` | The single migration environment resolves its URL from global settings |
| `app/models/chunk_embedding.py` | Embeddings are JSON with uniqueness by chunk/model; the local design needs full profile identity and numeric storage |
| `app/retrieval/keyword.py` | SQLite performs Python substring scoring, not the planned FTS5 search |
| `app/retrieval/hybrid.py`, `vector.py` | Filtered branches can generate the same query embedding repeatedly |
| `app/answer/service.py`, logging modules | Answer generation depends on a database user and persists raw question/answer data |
| `app/api/query.py`, `app/limits/*` | Route-specific controls do not establish one shared deadline/accounting boundary |
| `app/ingestion/*`, `app/cli/*` | Useful parsers/runners exist, but storage, job progress, and settings are coupled |
| `app/hpd/search.py` | Default property lookup depends on the large local/remote SQL table |
| `pyproject.toml`, `Dockerfile` | No console entry point/build configuration; cloud dependencies are installed with the core |

The existing [MVP plan](MVP_Implementation_Plan.md) is historical context.
The [answer quality plan](Answer_Quality_Implementation_Plan.md),
[legal question set](Legal_Review_Question_Set.md), and
[recorded legal review](Legal_Review_Technical_Results_2026-07-12.md) supply
regressions and review cases, not a substitute for the local release gates.

### 2.1 Proposed code organization

Names below are proposed destinations, not files claimed to exist. Keep a
module only when it establishes a real boundary; avoid copying old services
into parallel implementations unnecessarily.

| Proposed area | Responsibility | Existing code to adapt |
| --- | --- | --- |
| `app/workspace/` | Paths, settings resolution, workspace identity, application context, maintenance lock | `core/config.py`, `db/session.py` |
| `app/storage/` | Corpus/state repositories, schema coordinators, local artifacts, backup interfaces | `models/`, `db/`, `ingestion/artifacts.py` |
| `app/storage/migrations/corpus/`, `state/` | Independent local schema histories | Existing migrations retained for legacy mode |
| `app/jobs/` | Durable metadata, leases, checkpoints, cancellation, in-memory result store | `ingestion/runners.py`, ingestion-run models |
| `app/providers/` | Credential resolution, profiles, network policy, metered calls | `answer/providers.py`, `retrieval/embeddings.py` |
| `app/usage/` | Append-only accounting events, budgets, reservations, reports | `limits/service.py` and log-derived cost calculations |
| `app/local_access/` | Launcher token exchange, session, origin/Host/CSRF checks | `auth/` for reusable primitives only |
| `app/corpus/` | Generations, manifests, activation, canonical import/export | `ingestion/`, citation/source models |
| `app/hpd/` | Typed repository contract, live adapter, identity resolution, cache | Existing HPD search/routing/schemas |
| `app/api/v1/` | New local contracts and error mapping | Existing API routers delegate where compatible |
| `app/cli/main.py` | Unified console entry point; thin command adapters | Existing argparse-based CLI modules |
| `app/resources/` | Packaged manifests, synthetic demo, supported profiles, evaluation assets | Constants and checkout-relative fixtures |
| `tests/integration/`, `tests/e2e/`, `tests/performance/` | Additional migration, browser, failure-injection, and capacity tests | Keep useful existing tests |

Keep SQLAlchemy and the existing lightweight browser stack. A small standard
library CLI dispatcher can compose the existing commands; introducing a CLI
framework is not a dependency of the overhaul. Select/build-lock any numeric,
credential-store, and user-directory adapters during A03/C02 and verify wheels
on supported platforms before making them core dependencies.

### 2.2 Contracts to settle early

- `WorkspaceContext`: resolved paths, corpus/state session factories, network
  policy, profiles/credential resolver, usage service, job coordinator.
- `RequestContext`: operation ID, workspace identity, captured generation and
  embedding profile, monotonic deadline, cancellation signal, caller capability.
- `CorpusRepository`: acquire generation, filter evidence, read provenance,
  stage/validate/activate, calculate active versus retained readiness.
- `PropertyRepository`: resolve identity, fetch typed page, return source/cache
  provenance, completeness, and a request-bound continuation cursor.
- `ProviderGateway`: estimate/reserve/execute/settle; returns typed usage and
  failure information rather than provider exceptions containing raw payloads.
- `JobRecord` and `UsageEvent`: versioned enums and sanitized metadata, with
  no question text or provider secrets required for restart recovery.

These can be Python protocols/dataclasses and existing validation models.
Business services receive dependencies explicitly; CLI and HTTP adapters do
not implement separate versions of the business rules.

## 3. Phase A — Safe baseline and packaging foundation

### A01 — Protect the existing work and establish an isolated baseline

**Maps to:** F13, F14. **Depends on:** nothing.

Implementation:

1. Inventory tracked/untracked changes and migrations before editing. Reconcile
   the already-applied `20260712_0008` and `20260713_0009` files into a reviewed
   baseline without rewriting applied revisions or discarding user work.
2. Fix the test bootstrap first: override unsafe inherited environment values
   before importing application modules; use unique per-run temporary paths.
   Assert that destructive fixture operations target the marked test workspace.
   A fake environment label alone is not sufficient proof of isolation.
3. Add a subprocess regression with a deliberately unusable remote URL in the
   parent environment. Prove tests neither connect to it nor use shared `/tmp`
   artifacts. Deny unexpected network requests in ordinary tests.
4. Re-run the isolated existing tests and lint; repair confirmed failures and
   record new baseline results. Do not present the earlier test count as fresh
   evidence. Preserve migration tests separately from ORM `create_all` tests.
5. Record the comparator code revision, corpus/profile identities, review cases,
   and ranking configuration before changing retrieval. Keep actual corpus
   artifacts private unless redistribution has been reviewed.
6. Prepare a minimal offline CI job. Public repository creation, remote setup,
   committing, or pushing remains a separate authorized action.

Verification/output:

- Test environment cannot target the developer's database, provider, or bucket.
- Existing migration graph and lint/test results are documented and reproducible.
- Baseline retrieval inputs are identified; B04 supplies the canonical artifact
  export needed to replay them independently.

### A02 — Make workspace selection explicit and remove import-time side effects

**Maps to:** F01, F03, F11, F13. **Depends on:** A01.

Implementation:

1. Introduce the local workspace identity and OS-appropriate config/data paths.
   Resolve CLI `--data-dir`/`--config` first, then explicitly supported local
   settings/environment inputs, then local defaults. Document precedence.
2. Split configuration into local settings and an explicit legacy connection
   profile. Default local mode ignores legacy backend/provider values, including
   those in the checkout's `.env`; a migration command can select them explicitly.
3. Refactor app/engine/provider construction into factories. Merely importing
   `app.main`, CLI modules, or models must not create runtime files, connect to a
   database, load a paid provider, or configure global services for all workspaces.
4. Inject `WorkspaceContext` through app lifespan/dependencies and CLI handlers.
   Make two workspaces in one process independent, including credential lookup,
   cached settings, artifact roots, budgets, and provider selection.
5. Define one outbound HTTP policy used by downloads, providers, HPD, and update
   checks. Offline mode blocks remote egress before requests are constructed.
6. Retain explicit legacy entry paths while migrating callers. Do not expose
   unauthenticated legacy routes through the new local app as a shortcut.

Verification/output:

- Import and startup tests pass with poisoned legacy environment values.
- Two app instances do not share data/configuration accidentally.
- Missing keys and an empty corpus are readiness states, not startup exceptions.
- An offline synthetic workspace performs no external network traffic.

### A03 — Package the application and introduce the local launcher

**Maps to:** F01, F09 foundation, F14. **Depends on:** A02.

Implementation:

1. Add a package build configuration and `nyc-housing` console entry point.
   Preserve existing commands as adapters while introducing the specification's
   names. Use shared option parsing and documented exit categories.
2. Pin the tested Python minor version, initially 3.12, and align project metadata,
   lint targets, CI, and the lockfile. Preserve the current `dev` extra initially
   so `uv sync --locked --extra dev` stays coherent.
3. Move PostgreSQL/S3-only dependencies into explicit legacy extras and remove
   unconditional imports of them from core startup. Inspect authentication
   dependencies before deciding whether they also belong only in legacy mode.
4. Include templates, static files, local migration resources, manifests, and
   required demo/evaluation assets in built packages. Replace checkout-relative
   runtime fixture paths with package-resource access.
5. Implement idempotent setup scaffolding and loopback `serve`, port selection,
   browser launch, `--no-browser`, and actionable errors. Never auto-start paid
   indexing. Setup will gain real corpus/key steps in B03/C02.
6. Add a clearly synthetic demo and a read-only Setup/Sources shell. Until C01
   lands, do not expose writable settings, exports, or paid endpoints through it.
7. Test installed wheels/sdists outside the checkout, not just editable installs.

Verification/output:

- A clean dependency install starts the synthetic shell without cloud extras.
- Package assets load from an unrelated working directory and Unicode/spaced paths.
- The native launcher rejects non-loopback binding; browser-open failure is benign.
- **Phase A exit:** a reproducible local shell, isolated tests, and explicit
  configuration boundaries exist without changing the legacy environment.

## 4. Phase B — Local storage, corpus lifecycle, and search

### B01 — Introduce corpus/state schemas and transaction boundaries

**Maps to:** F03, F04 foundation, F10 foundation, F13. **Depends on:** A02, A03.

Implementation:

1. Add separate SQLAlchemy metadata/session factories and local migration
   histories for `corpus.sqlite3` and `state.sqlite3`. Keep existing legacy
   revisions in place; do not run their monolithic HPD/auth schema into the
   new core databases or stamp a fresh local database as a legacy database.
2. Create corpus tables for immutable source content, generation membership,
   citations/evidence, embedding profiles, numeric vectors, and a single active
   generation pointer. Separate mutable check history from immutable content.
   Establish generation-scoped FTS5 schema/build hooks here; B03/B04 populate
   them, while B05 implements and evaluates the full retrieval behavior.
3. Create state tables for nonsecret settings, job metadata/leases, usage events,
   cache metadata, and local-access state. Store cross-database references as
   explicit IDs validated by services, not unenforceable cross-database FKs.
4. Configure foreign keys on every connection, local WAL, bounded busy waits,
   appropriate connection lifecycle, and write serialization. Keep any in-memory
   test pool behavior separate from production file-backed connections.
5. Store vectors as declared numeric bytes with dimension, normalization,
   checksum, and complete preprocessing/provider/model profile identity.
   Validate finite values, dimensions, and norms before indexing or import.
6. Implement consistent pre-migration backup, schema-version preflight, and
   staged initialization. A workspace manifest records both schema versions;
   startup refuses partially upgraded/incompatible pairs until recovery.
7. Define reset scopes and exact-target confirmations. Corpus rebuild/reset
   does not touch usage, keys, exports, or another workspace.

Transaction rules:

- Network work occurs outside long database write transactions.
- Corpus activation changes one pointer in the corpus database only. A crash
  before the matching state-job update is reconciled by inspecting that pointer.
- Usage reservation is committed in state before a paid request; a corpus
  rollback cannot undo that reservation or settlement.
- Before pruning, account for active/previous generations, running query leases,
  staging jobs, and explicitly pinned evidence. No writer deletes in-use bytes.

Verification/output:

- Fresh and upgrade migrations run against real temporary SQLite files.
- Crash/failure injection never exposes a half-initialized workspace as ready.
- Concurrent read/write tests preserve generation and FK invariants.
- Separate workspace and schema-reset tests preserve the ledger and exports.

### B02 — Implement resumable maintenance jobs and runtime cancellation

**Maps to:** F03, F04, F10, F12. **Depends on:** B01.

Implementation:

1. Implement the specified job state machine, stable operation IDs, bounded
   sanitized errors, checkpoints, lease owner/heartbeat, and a workspace writer
   coordination mechanism shared by CLI and web processes.
2. Let `serve` own its runner; a standalone CLI runner participates in the same
   leases and fails/queues clearly on conflict. Do not require a queue service
   or silently allow a second writer because it is another OS process.
3. Checkpoint stages/batches using idempotent identities. If the process dies
   between a corpus commit and job-progress update, discover committed work
   from corpus records rather than repeating it blindly.
4. On restart, pause interrupted maintenance jobs for explicit resume. Mark
   interactive jobs with lost memory-only payloads failed/resubmittable; never
   replay a paid question automatically.
5. Introduce monotonic deadlines and cancellation signals through shared
   services. Move blocking I/O off the event loop or use cancellable adapters;
   cancellation must not mean only checking elapsed time after completion.
6. Implement job list/resume/cancel CLI and status contracts. Persist no raw
   interactive question/context/answer. Add a bounded in-memory payload/result
   store with the specification's 30-minute inactivity expiry.
7. Keep leases/uncertain spend distinct: an expired worker lease does not prove
   a provider never received a billable request. C03 supplies accounting recovery.

Verification/output:

- Kill/restart at batch boundaries resumes committed public-source work once.
- Separate CLI/web processes cannot concurrently mutate the same generation.
- Cancellation is acknowledged within the 1-second local target; no new
  batches are admitted afterward. Lost answer payloads give a resubmit state.

### B03 — Refactor source installation and atomic generation activation

**Maps to:** F04, F12, F01 setup integration. **Depends on:** B01, B02.

Implementation:

1. Convert the five core source definitions into versioned manifests containing
   scope, publisher/URLs, parser contract, anchors, and access/redistribution
   review status. Keep HPD violations out of the core legal installation list.
2. Adapt current AmLegal XML, Senate legal-text, and HPD guidance parsers into
   functions producing canonical records without selecting the active version
   or committing unrelated application state.
3. Stage download, parse, validate, keyword-index, embedding-index, and activate
   separately. B03 initially supports explicit text-only activation; real paid
   indexing remains disabled until C03/C04.
4. Persist original artifacts by content hash with manifest-relative paths and
   atomic file publication. Validate archive paths/sizes and recursively verify
   HPD guidance page references, not just the top-level JSON manifest.
5. Use content/profile identity to reuse unchanged data. Record last successful
   check separately from fetched/effective dates. Conditional/unchanged updates
   must not create duplicate chunks or future embedding charges.
6. Validate anchors, citation paths, nonempty extraction, duplicate IDs, and
   abnormal changes before activation. Produce a per-source diff/report; a
   failed source preserves its previous usable version.
7. Implement `corpus install`, `update`, `verify`, `activate`, `rollback`, and
   source-readiness summaries. Prompt for deliberate partial/text-only activation.
8. Support manual official-artifact import when automation fails, retaining
   provenance and limitations. Freeze/pin generations and retain at least the
   active and previous generation; prune only unreferenced artifacts.

Verification/output:

- Identical artifacts reproduce the baseline coverage; newer source differences
  are reported, not treated as an unconditional 690-chunk requirement.
- Empty/broken parsers, missing nested artifacts, disk-full writes, and cancellation
  preserve current retrieval. An unchanged refresh schedules no embedding work.
- Source-only setup can acquire and inspect text without a key; every missing
  core source is visible. Integrated production search is the B05 gate.

### B04 — Build canonical bundles and read-only legacy migration

**Maps to:** F04 import, F13. **Depends on:** B01–B03.

Implementation:

1. Define a versioned canonical bundle: JSON manifests/records plus referenced
   artifacts and numeric vector files. Specify hashes, size limits, profile
   identity, source membership, and a migration summary.
2. Implement an explicitly selected legacy exporter using a read-only database
   transaction/credential where possible. Freeze selected source-version IDs
   and evidence within a consistent read; copy only required object-store
   artifacts. Do not call existing write/log/ingest services to export.
3. Export current legal content by default. Exclude HPD bulk rows, users,
   sessions, secrets, questions, and logs. Make historical versions an explicit
   scope and report them separately.
4. Verify artifact bytes and recursively rewrite nested references to portable
   paths. Validate embedding text hashes, dimensions, and complete profile
   provenance; if legacy preprocessing is unknown, quarantine those vectors
   for verification instead of inventing compatible metadata.
5. Import into staged data in a new workspace, validate all references and
   counts, build local indexes, and activate only after validation. Reject path
   traversal, symlinks/escape paths, excessive extraction, corrupt hashes, or
   executable bundle content. Never execute bundled SQL/Python.
6. Report reused versus unavailable/incompatible vectors and the source-only
   capability that remains. Re-embedding requires later explicit consent.
7. Preserve the original environment and produce a comparison manifest. Keep
   migration outputs private/local unless separately approved for redistribution.

Verification/output:

- A representative legacy fixture round-trips exact active evidence/provenance.
- When explicitly authorized, verify the assessed real source set read-only;
  measure any drift from the recorded baseline and explain it.
- Corrupt bundles fail before affecting an active workspace; exports contain
  no excluded tables, credentials, or private absolute paths.
- Frozen artifacts/profile metadata now support B05's PostgreSQL comparator.

### B05 — Implement production local retrieval and prove parity

**Maps to:** F05, F06 evidence foundation. **Depends on:** B01, B03, B04.

Implementation:

1. Preserve exact citation normalization and indexed citation lookup. Implement
   FTS5 weighted title/citation/body retrieval with bound inputs and safe handling
   of punctuation, empty queries, and FTS query syntax.
2. Use generation-scoped index builds so staging/historical content cannot
   change the active generation's search statistics or leak into candidates.
   Map evidence IDs to FTS rows explicitly; validate any generated table names
   from internal IDs, never user-provided identifiers.
3. Apply source/jurisdiction/generation filters before candidate limits. Handle
   SQLite BM25's rank direction deliberately and fuse ranks deterministically
   rather than mixing incomparable raw scores.
4. Implement exact normalized numeric-vector ranking behind an index interface.
   Cache immutable matrices by generation/profile, bound memory, and release
   old matrices only after in-flight readers finish.
5. Compute one query embedding per question/profile/request and pass the vector
   to all filtered branches. In this phase use fake/precomputed vectors; inject
   the metered real gateway in C04.
6. Make missing/failed semantic retrieval a labeled keyword/citation fallback.
   Surface text-ready versus embedding-ready counts; never interpret missing
   embeddings as absent legal text.
7. Expand the five-item evaluator and 28-question review into at least 50 labeled
   retrieval cases, with held-out paraphrases and evidence expectations. Keep
   model-answer evaluation separate from deterministic retrieval evaluation.
8. Replay the frozen PostgreSQL ranking implementation against an isolated
   comparator database or controlled read-only retrieval harness using the same
   artifacts/query vectors. Do not require PostgreSQL in normal core CI or invoke
   the existing logging answer endpoint to obtain a retrieval baseline.

Verification/output:

- Recall@5 and MRR are no more than 0.02 below the frozen comparator on the
  identical labeled set; designated exact-citation cases rank correctly.
- Changing generations mid-request never mixes evidence or vector profiles.
- Local search benchmarks cover both current-scale and 10,000-chunk fixtures.
- **Phase B exit:** a clean keyless installation can acquire/search core text;
  a legacy user can import compatible evidence without changing remote data.

## 5. Phase C — Local access, paid operations, and answer experience

### C01 — Implement the local-session security boundary

**Maps to:** F11, F09 setup. **Depends on:** A03, B01.

Implementation:

1. Create per-installation secret/session state and one-use short-expiry launcher
   tokens. Exchange a fragment token via same-origin POST, clear it immediately,
   and issue an HttpOnly SameSite cookie. Provide the one-time code flow for
   `--no-browser`; do not put bootstrap values in request/access logs.
2. Enforce the allowed loopback Host/origin, CSRF protection on state-changing
   and paid operations, session gates on sensitive GETs, and no wildcard CORS.
   Reject token replay and cross-origin/rebinding attempts before business logic.
3. Separate unauthenticated static bootstrap assets from protected data routes.
   Native LAN binding remains unsupported. Keep legacy authentication behind
   an explicit legacy application entry point.
4. Apply controls to settings, jobs, exports, credentials, query/search, and
   property routes consistently. CLI uses OS-local workspace access rather
   than an anonymous HTTP bypass; it still uses the same accounting/services.
5. Regenerate launch state on restart. Redact exceptions, request metadata,
   tokens, credentials, and response bodies in the logging boundary.

Verification/output:

- Missing/expired/replayed tokens and hostile Host/Origin requests cannot read
  data, change settings, trigger network/provider calls, or create exports.
- Legitimate launch/browser restart/no-browser flows pass end-to-end.
- No claim is made to isolate the app from malware running as the same OS user.

### C02 — Implement credentials and versioned provider profiles

**Maps to:** F02, F11, F01 setup. **Depends on:** A02, B01, C01.

Implementation:

1. Add an OS credential-store adapter with explicit availability/error reporting.
   Scope credentials to installation/workspace identity. Support environment
   resolution and a documented user-managed secret-file fallback without silent
   plaintext persistence in ordinary settings.
2. Implement hidden-input CLI setup and protected write-only UI credential
   replacement. Redacted APIs report presence/provider only. Treat optional
   Socrata tokens as secrets under the same storage/redaction rules.
3. Add answer/embedding profiles with model/provider/endpoint, preprocessing,
   dimensions, context/output/tokenizer policy, capabilities, and dated prices.
   Capture old configuration as migration input, not a newly endorsed default.
4. Support one default credential and advanced separate credentials/endpoints.
   Answer-model changes preserve embeddings; embedding-profile changes require
   a compatible index and explicitly staged work.
5. Add missing/invalid/exhausted/provider-unavailable states. Until C03 lands,
   tests use fake validation; no real paid validation bypasses metering.
6. Select the release profile using the existing evidence suite and current
   primary provider documentation during implementation. Record the decision
   and prices; unknown/unverified pricing cannot enable unattended paid batches.

Verification/output:

- Keys never appear in settings responses, diagnostics, package fixtures, logs,
  browser persistence, or shell arguments.
- Credential-store failure leaves a supported explicit fallback and usable
  keyword search. Provider/profile mismatch fails before vector comparison.

### C03 — Centralize spend reservations, provider execution, and deadlines

**Maps to:** F10, F02 validation, F12 job accounting. **Depends on:** B02, C02.

Implementation:

1. Implement append-only reserve/settle/uncertain/correction events in state,
   linked by operation and attempt IDs. Derive monthly totals from events;
   use integer minor/micro currency units or exact decimals, not binary floats.
2. Reserve projected input/output cost atomically under a short state write
   transaction. Enforce the installation monthly cap and per-operation ceiling
   across CLI/web processes, including outstanding and uncertain reservations.
3. Define the budget month/timezone explicitly and test month rollover. Retain
   active reservations across reporting-period changes; never discard them
   merely because the calendar rolled or a worker lease expired.
4. Implement the provider gateway: estimate, reserve, send with deadline, capture
   returned usage, settle, sanitize failure. Snapshot profile prices with each
   attempt and preserve conservative uncertainty after lost responses.
5. Meter retries as attempts and avoid unsafe automatic replay when billing is
   uncertain. Completed local batches are reusable; a timeout is not proof of
   free work. Test crash windows before send and after remote/local completion.
6. Apply the specification's USD 15 monthly default, two concurrent paid calls,
   batch approvals, and unknown-price behavior. Make concurrency limits shared
   across CLI and server processes rather than separate in-memory semaphores.
7. Enforce the total answer deadline, per-attempt remaining time, and cancellation
   in this shared layer. Do not hold database locks during HTTP requests.
8. Introduce `usage` reporting and tests proving history deletion, corpus rollback,
   and key replacement do not erase accounting. Document that other applications
   using the same key remain outside this installation's budget.

Verification/output:

- Simultaneous processes cannot over-admit requests against the same cap.
- Query/corpus embeddings, answers, summaries, evaluator/debug commands, and
  validation all pass through the gateway; test each entry path explicitly.
- Deadline/cancel/lost-response tests retain justified accounting and release
  local capacity without pretending to reverse an external provider charge.

### C04 — Integrate metered embeddings and grounded answer jobs

**Maps to:** F04 paid indexing, F05 semantic search, F06, F10, F12.
**Depends on:** B03–B05, C03.

Implementation:

1. Connect the provider gateway to corpus embedding batches and the single
   per-request query embedding. Reuse completed content/profile matches and
   activate a semantic generation only after its declared coverage validates.
2. Refactor `generate_answer` to receive request/workspace context, retrieved
   evidence, and a provider dependency; remove mandatory database-user and raw
   answer/retrieval-log dependencies from local mode.
3. Start tracked asynchronous answer jobs with memory-only interactive payloads.
   Return evidence as soon as retrieval finishes, then provider progress/result.
   Browser polling does not reset result expiry indefinitely without real user
   activity; losing the process requires explicit question resubmission.
4. Build context with source-aware subsection/excerpt selection and token/output
   budgets. Preserve qualifications, map public citation markers to the exact
   supplied evidence, and keep UUIDs out of ordinary answer prose.
5. Generate coverage descriptions from the selected generation, not a hardcoded
   list of every source ever planned. Distinguish insufficient coverage, missing
   facts, unsupported historical period, and provider/budget failure.
6. Preserve source reading on provider failure; never show a truncated response
   as complete. Treat retrieved text as untrusted evidence, not instructions.
7. Refactor evaluator/debug CLI callers onto the same execution path with explicit
   cost ceilings. Do not seed synthetic users or disable caps to make them work.
8. Run the expanded legal answer evaluation with a bounded approved budget;
   obtain domain review for designated must-pass claims/qualifications. Citation
   ID validity and keyword matches alone do not establish legal correctness.

Verification/output:

- All displayed citations resolve to excerpts actually supplied for that answer.
- Existing weak scenarios receive explicit fixes/disposition and required review.
- Real provider failures/budget denial still return useful local evidence.
- Interrupted indexing resumes committed batches; interrupted answers do not
  silently persist transcripts or re-spend on restart.

### C05 — Complete keyed setup and the research UI/API slice

**Maps to:** F01, F09, F06, F11. **Depends on:** C01–C04.

Implementation:

1. Wire the setup journey: data location, truthful external-data-flow disclosure,
   optional key/profile, budget, source installation, readiness, first question.
   Source-only/demo choices remain usable and clearly labeled.
2. Implement `/api/v1` search/query/settings/credentials/usage/job contracts and
   the Research, Sources, and Settings navigation. Keep endpoint handlers thin.
3. Add explicit Answer versus Search sources mode, source filters, evidence
   inspection, readable citation markers, cancellation, retry/resubmit, and
   basic Markdown/JSON research export from the current result.
4. Display generation/profile, incomplete source/index readiness, stale check
   dates, and cost/provider errors without overwhelming everyday copy.
5. Render all external text safely, restrict citation links, and implement
   keyboard/focus/form/error/table accessibility and narrow-screen behavior.
6. Add browser coverage for first-run, no-key, first answer, source inspection,
   budget change, cancel, result expiry, restart, and malformed responses.

Verification/output:

- A new user can complete setup and ask/export a cited question without manual
  SQL, admin-user creation, or editing a database/storage environment file.
- **Phase C exit:** keyed legal research is secure, metered, source-inspectable,
  and meaningfully usable; no claim of full L1 completion is made yet.

## 6. Phase D — Live and cached property research

### D01 — Implement the typed HPD connector and identity resolution

**Maps to:** F07. **Depends on:** A02, B01; C01/C02 before protected/token UI.

Implementation:

1. Define the property repository and response contract independently of SQL
   tables: input identifiers, filters, resolved identity/candidates, records,
   source timestamps, connector version, completeness, and continuation.
2. Create a versioned manifest for the official HPD dataset and explicit field
   projection. Verify schema, anonymous/token access, status fields, IDs, and
   deterministic ordering through a small bounded live contract check.
3. Start with the specification's SODA2.1 preference only if verified. Keep API
   dialect behind the adapter; if identification is necessary, guide user-token
   setup without disabling legal research or cached property results.
4. Build requests from typed allowlisted fields/operators and escaped literals.
   Never accept arbitrary SoQL, SQL, or a user-provided pagination URL to fetch.
5. Implement building/registration ID and structured address resolution. Preserve
   meaningful house-number hyphens/suffixes; normalize known street aliases;
   return concrete building candidates when an input is ambiguous.
6. Do not equate no matching violation records with proof that an address/building
   does not exist. This dataset is not a complete address registry. Clarify input
   or report no matching records with its source/filter scope.
7. Implement default 50/max 100 rows, unique ordering, request-bound continuation,
   duplicate handling, optional totals, shared outbound concurrency, retries,
   `Retry-After`, and the 15-second interactive-page deadline.
8. Preserve original status/date fields and document simplified filters. BBL and
   property legal-regime classification remain unsupported in this connector.

Verification/output:

- Fixture cases cover positive/zero, ambiguous borough, registration-to-building
  ambiguity, aliases/hyphens, malformed identifiers, filters, pagination, and
  source schema/auth/rate-limit errors.
- A normal lookup performs only bounded filtered remote requests; no exact
  global count or bulk ingestion is required for first-page rendering.
- Use a verified property for live checks and frozen synthetic/permitted records
  for deterministic tests; do not assume the old Front Stagg example is valid.

### D02 — Add cache provenance, freshness, and bounded exports

**Maps to:** F07, F11, F12. **Depends on:** D01, B02.

Implementation:

1. Cache typed response pages/artifacts by property, every filter, connector/schema
   version, and pagination identity. Include fetch interval, source date when
   available, row IDs, continuation, and completeness metadata.
2. Apply the specified defaults: successful data fresh for 24 hours, verified
   empty responses for 1 hour, stale success retention up to 30 days, 500 MB
   default cap. Track access without treating a cache hit as a new agency fetch.
3. Keep transport/schema/rate-limit failure out of negative-result caching.
   Distinguish cache miss, verified zero, partial pages, stale success, and an
   unavailable source in both repository results and UI schemas.
4. Evict least-recently-used unpinned entries; protect records backing an active
   summary/export until it finishes. Warn when pins prevent meeting the cap.
5. Implement Refresh/Next page and explicit export scope. Export loaded pages
   immediately; a user-requested complete fetch is a bounded cancellable job
   with progress and partial-result labeling if interrupted.
6. Explain that pages fetched over time from the live source may not be a
   transactionally consistent snapshot. No export silently claims otherwise.
7. Integrate cache-only offline reads and explicit cache-clearing controls;
   redact researched addresses from default diagnostics.

Verification/output:

- A timed-out later page cannot make an earlier page appear complete or empty.
- Filter/connector changes cannot hit an incompatible cached response.
- Stale/offline exports retain the same age, scope, and completeness labels.
- Cache expiry/eviction/cancel tests do not delete active evidence or user exports.

### D03 — Integrate property research and combined evidence in the UI

**Maps to:** F07, F09, F06 combined answers. **Depends on:** D02, C04, C05.

Implementation:

1. Adapt existing query routing to the typed repository. Permit explicit mode
   selection; a legal question mentioning a property must not force a property
   lookup or infer facts from an unconfirmed address.
2. Add candidate selection, filter controls, property evidence table, pagination,
   freshness/completeness labels, source links, and scoped exports.
3. Implement an explicit metered Summarize action on a fixed identified result
   set. Record which rows and legal excerpts were actually included in context.
4. Keep property and law evidence separately identified; report omitted rows,
   filters, fetch dates, and context limits. Never infer unobserved violations,
   current unresolved status, or legal eligibility without supporting evidence.
5. Complete `/api/v1/properties/search` and `/summarize` and the routed-query
   clarification contract. Add fixture-backed browser tests for the full flow.

Verification/output:

- A user resolves an ambiguous address, pages results, disconnects, views an
  honest cached result, and optionally generates an evidence-backed summary.
- Provider failure leaves the property records accessible with no new lookup
  or summary charge just to inspect evidence.
- **Phase D exit:** address-specific research works without a full HPD database;
  unsupported citywide analysis is explicit rather than approximated from cache.

## 7. Phase E — Recovery, operational readiness, and L1 release

### E01 — Finish maintenance, backup/restore, and diagnostic tools

**Maps to:** F03, F12, F11, F09 maintenance. **Depends on:** B01–B04, C03–C05, D02.

Implementation:

1. Finish `status`, `sources`, `doctor`, and `corpus verify` using maintained
   summaries/indexed metadata. Expose active versus retained counts, text/vector
   readiness, schema/profile versions, partial sources, and interrupted jobs.
2. Make `doctor` read-only by default. Separate filesystem/runtime checks from
   bounded `--online` checks and separately selected paid validation. Identify
   detectable unsupported storage locations and document detection limits.
3. Finish Sources/job UI: stage progress, update/check dates, estimates/approval,
   retry/resume/cancel, partial activation, rollback, and maintenance conflicts.
4. Implement a consistent multi-store backup: enter a maintenance barrier, stop
   admitting writes/new jobs, finish or safely pause work, capture both databases
   with their referenced immutable artifacts and manifest, then verify hashes.
   Copying two live database files at unrelated instants is not the protocol.
5. Exclude secrets, session/bootstrap state, memory-only transcripts, and unrelated
   exports from default backups. Explicitly describe optional artifact/cache scope.
6. Stage restore into a selected destination, verify schema compatibility and
   references, and replace only an explicitly approved exact workspace. Never
   attempt an automatic destructive schema downgrade.
7. Preserve newer local usage events when restoring older corpus/application
   data into an existing installation; merge immutable event IDs or retain the
   current ledger. A recovered installation with incomplete billing history
   must disclose that its historical estimate is incomplete, not claim that
   restoring an old backup resets provider spend.
8. Complete Markdown/JSON research exports and property CSV exports. Neutralize
   spreadsheet formulas, label pagination/filter scope, and retain source dates,
   evidence IDs, model/profile/prompt versions, and generation identity.
9. Implement redacted diagnostics and configurable log/cache/usage retention.
   Keep reservations needed for active accounting even when pruning old logs.
10. Add manual application update checks and the documented stop/backup/tagged
    update/locked-sync/preflight/restart workflow. Never mutate a dirty checkout
    or run downloaded code as part of checking for an update.

Verification/output:

- Crash, restore, failed migration, insufficient disk, corrupt backup, interrupted
  job, and old-code/new-schema drills have tested recovery paths.
- Backup/diagnostic archive inspection finds no secrets/session tokens or
  default-history transcripts. Restoring does not silently undercount known spend.
- Status/doctor never performs a citywide table scan or silent paid operation.
- Source reminders default to 30 days; optional while-open checks do not enable
  paid work or claim to run while the app is closed.

### E02 — Prove quality, performance, and clean-machine compatibility

**Maps to:** F01, F05, F06, F09, F14 and all L1 acceptance gates.
**Depends on:** integrated Phases B–D, E01.

Implementation:

1. Expand CI to clean native macOS, Windows, and Linux environments, using
   the released Python/lockfile and unique data roots. Verify each advertised
   architecture; record manual evidence if a suitable CI runner is unavailable.
2. Test both the documented clone/uv workflow and built packages in a fresh
   environment without the source checkout, editable imports, or developer keys.
3. Run unit, migration, service, multi-process concurrency, browser, security,
   privacy, offline-egress, packaging, and fault-injection suites. No default
   test job receives real provider/database credentials.
4. Measure the specified 8 GB/SSD reference targets: idle app RSS below 500 MB,
   installed startup below 5 seconds, keyword p95 below 500 ms, local hybrid
   ranking p95 below 1 second at 10,000 x 1,536 vectors, excluding remote embedding.
   Report actual core/staging disk use and total installation conditions.
5. Benchmark cold/warm conditions and concurrent update/read behavior separately.
   Measure deadline/cancellation latency and evidence-before-answer behavior.
   Do not shrink coverage or silently skip a slow case to meet a target.
6. Run explicitly invoked bounded live-source contract checks and approved paid
   answer evaluations separately. Record dates, artifact/profile hashes, prices,
   cost, limitations, and reviewer decisions.
7. Review every designated must-pass legal scenario and held-out retrieval case.
   Resolve material correctness issues; matching an old weakness is not parity
   success sufficient for release.
8. Exercise a clean-machine journey with someone other than the implementer:
   setup, keyless search, key setup, first answer, property lookup, update,
   export, restart, and recovery using only the shipped documentation.

Verification/output:

- An L1 acceptance report links every gate to repeatable evidence, not checkboxes
  justified only by code inspection.
- A platform is advertised only after its full required installation/browser
  evidence exists. Missing machines or reviewer availability remain visible
  release dependencies, not silently waived tests.

### E03 — Finish documentation and prepare the release handoff

**Maps to:** F14, F01, F13. **Depends on:** E01, E02, maintainer decisions.

Implementation:

1. Replace the current README quickstart only after the new commands work.
   Link dedicated Setup, Coverage, Privacy/Data Flow, Costs, Updating,
   Troubleshooting, Migration, and Contributing documentation.
2. Mark conflicting hosted runbooks as historical while retaining useful legacy
   migration information. Remove stale default models/limits and commands from
   active instructions; keep an advanced environment example without secrets.
3. Document prerequisites, optional source token, supported OS/architectures,
   ordinary versus bulk footprint, offline limitations, and known source gaps.
   Explain that local operation removes maintainer hosting, not API costs or
   ongoing parser/release maintenance.
4. Obtain the maintainer's code-license decision and review dependency/source
   attribution. Do not publish corpus assets without a source-specific recorded
   redistribution decision. Default source-download installation must not depend
   on an optional prebuilt bundle being available.
5. Audit the Git/release contents for secrets, personal paths, databases, source
   downloads, histories, caches, and private migration outputs. Verify optional
   asset size/platform constraints at publication time rather than hardcoding
   an outdated hosting assumption.
6. Prepare versioned release notes, model/source manifests, acceptance report,
   upgrade/rollback compatibility table, known issues, and support/reporting
   instructions. Verify the actual repository URL before publishing commands.
7. Present the release candidate for explicit publication. Do not delete the old
   database or bucket, rotate credentials, create a public repository, or push
   content as an unannounced cleanup step.

Verification/output:

- **Phase E exit / L1 gate:** all feature-specification L1 gates pass; all required
  decisions are recorded; a new user can run the supported application without
  maintainer infrastructure. Publication remains a separate authorized action.

## 8. Phase F — Optional extensions, separately releasable

Work IDs use **X** here to avoid confusion with specification feature IDs F01–F17.
These packages must not increase default installation requirements.

### X01 — Full HPD snapshot and structured analytics

**Maps to:** F08. **Depends on:** L1 repositories/jobs/usage/recovery contracts.

1. Benchmark DuckDB/Parquet and indexed SQLite on representative/full authorized
   official data; record download, staging, retained disk, lookup/aggregate latency,
   memory, platform wheels, and recovery behavior. Select an engine by evidence.
2. Add an explicit extra and `data install/update/status/remove hpd` lifecycle.
   Show actual estimated footprint and progress before downloading; default
   setup, API cache misses, and legal source updates never call this importer.
3. Implement staged full snapshots and resumable verified deltas. Preserve mode
   on resume, exact timestamp/tie-breaker precision, and completed checkpoints.
4. Quarantine invalid/sentinel timestamps, including `9999-12-11`; verify a real
   change-tracking field or label status-date updates best-effort. Reconcile
   periodically against complete source exports, accounting for deletions.
5. Add typed bounded aggregate templates and ranking metrics with explicit filters,
   denominators, snapshot provenance, and export scope. No arbitrary model SQL.
6. Switch snapshots atomically, retain rollback, and provide explicit uninstall.

Acceptance: complete offline property/aggregate queries work with zero egress;
full/delta crash tests preserve IDs and scope; malformed dates cannot poison
watermarks; optional totals do not block first-page results; core remains lean.

### X02 — Additional official legal modules

**Maps to:** F15. **Depends on:** B03/B05/C04 source and evaluation contracts.

1. Prioritize unmet research questions from evaluations/user feedback, not raw
   corpus size. Evaluate official rent-regulation/DHCR/additional RPL sources
   separately for coverage, access, provenance, and redistribution.
2. Implement manifest, parser fixtures, normalization, source dependencies,
   anchors, update policy, and module-specific retrieval/answer cases.
3. Add install/remove/estimate/readiness UI and cross-source evaluation. Keep
   content unavailable until parsing/indexing/activation meets declared readiness.
4. Benchmark larger corpora before choosing an alternative vector index; do not
   silently prune sources to fit the original 690-chunk footprint.

Acceptance: a module installs without replacing core data/services and its
declared research capability is supported by reviewed evidence, not merely a URL.

### X03 — Optional Docker distribution

**Maps to:** F16. **Depends on:** supported L1 package and launcher contracts.

1. Rework the existing Dockerfile around the committed lockfile and packaged
   assets; supply a single-app container configuration with persistent volume.
2. Handle container secret input explicitly, run unprivileged where supported,
   and document bootstrap plus host-loopback versus container binding.
3. Test clean setup, restart/rebuild persistence, cancellation, backup/restore,
   and host network exposure. Do not require PostgreSQL/S3 sidecars.

Acceptance: the documented configuration is reachable locally but not exposed
to the LAN by default, and rebuilding the app does not erase its workspace.

### X04 — Opt-in saved research history

**Maps to:** F17 history. **Depends on:** C04 payload/provenance and E01 retention.

1. Add a separately opt-in persistence policy with retention and individual/all
   deletion. Migrate metadata without turning existing users' history on.
2. Save source excerpts and version/profile references; pin or snapshot necessary
   evidence so pruning does not silently destroy reproducibility.
3. Test disable/delete/export/backup behaviors and keep usage accounting separate.
   Re-running a question creates a new result with its own cost and provenance.

Acceptance: default L1 privacy is unchanged, opted-in history is recoverable
and deletable, and deleting research does not reset the spend ledger.

### X05 — Fully local models and optional local scheduling

**Maps to:** F17 local models; F12's deferred OS scheduling.
**Depends on:** C02–C04 provider/network contracts and E01 maintenance commands.

1. Publish a separately evaluated local answer/embedding profile with measured
   hardware/download/runtime requirements; configure loopback model endpoints
   explicitly and preserve network-policy enforcement.
2. Support local answers with local query embeddings or an explicit keyword-only
   retrieval profile. Missing local models must not cause remote fallback.
3. Document opt-in OS scheduling recipes for maintenance commands, job conflicts,
   credentials, and budgets. No scheduler is installed without explicit choice.

Acceptance: local-profile answer tests show zero remote egress and documented
quality; scheduled work uses the same locks/checkpoints/budgets as manual work.

## 9. Feature-to-work-package traceability

“Owner” means the implementation package responsible for closing the requirement,
not a person or a claim that the work has already been assigned.

| Specification feature | Primary implementation owner(s) | Closing evidence |
| --- | --- | --- |
| F01 Installation/startup | A02, A03, B03, C05, E02/E03 | Clean clone and packaged install; keyless/keyed first-run journeys |
| F02 Credentials/profiles | C02, C03 | Secret-storage tests, mismatch tests, metered validation |
| F03 Local persistence | B01/B02, E01 | Migration, isolation, concurrency, backup/restore drills |
| F04 Corpus/provenance | B03/B04, C04 | Unchanged-update, failed-activation, bundle traceability, embedding-resume tests |
| F05 Local retrieval | B05, C04, E02 | Exact/filtered cases, frozen parity report, 10,000-chunk benchmarks |
| F06 Grounded answers | C04/C05, D03, E02 | Supplied-evidence citation checks and reviewed answer scenarios |
| F07 Live HPD/cache | D01–D03 | Live contract, identity/pagination/cache fixture and browser tests |
| F08 Bulk extension | X01 | Offline full-data lookup/analytics and reconciliation/recovery evidence |
| F09 Browser experience | A03, C05, D03, E01/E02 | Complete browser journeys, accessibility, error-state tests |
| F10 Costs/deadlines | B02, C03/C04 | Multi-process cap, all-caller metering, timeout/uncertain-spend tests |
| F11 Access/privacy | A02, C01/C02, C04, E01 | Host/origin/CSRF, secret/transcript leakage, offline-egress tests |
| F12 Jobs/maintenance/exports | B02/B03, E01, X05 scheduling | Crash/resume, status, scoped exports, update/restore drills |
| F13 Migration | A01/A02, B01/B04, E03 | Reviewed baseline, poisoned-environment guard, read-only import/export |
| F14 Docs/CI/distribution | A01/A03, E02/E03 | Reproducible package/OS evidence and audited release candidate |
| F15 Additional sources | X02 | Independent module install and module-specific quality report |
| F16 Containers | X03 | Persistence/exposure and clean-container setup tests |
| F17 History/local models | X04/X05 | Opt-in retention and separately measured offline-answer profile |

## 10. Verification workflow and evidence

### 10.1 Test layers

| Layer | Required coverage | Network policy |
| --- | --- | --- |
| Unit | Citation/FTS input handling, profile identity, costs, dates, typed HPD filters, cache keys | Denied; synthetic data |
| File-backed integration | Actual migrations, FKs/WAL, generations, bundles, exports, backup/restore | Denied by default |
| Multi-process | CLI/web locks, spend reservations, leases, active-reader pruning protection | Fake services/local fixtures |
| Fault injection | Kill between checkpoints, disk full, corrupt content, migration failure, missing artifacts | Fake services/local fixtures |
| Browser | Setup, session, sources, answer/evidence, property ambiguity/paging, budget, cancel/export | Fake providers and HPD responses |
| Security/privacy | Host/Origin/CSRF, token replay, archive traversal, log/backup leakage, offline blocking | Explicit controlled local test origins |
| Retrieval/quality | Frozen comparator, held-out questions, exact citations, human-reviewed claims | Offline replay; paid evaluation separately approved |
| Packaging/platform | Clone/uv and wheel/sdist outside checkout, no cloud extras, supported OS/architecture | Dependency installation allowed; tests remain isolated |
| Release contracts | Official source/schema access, released provider profile | Explicit invocation, bounded requests/cost |

### 10.2 Proposed commands and execution safety

Commands below describe implementation acceptance once their packages exist;
they are not instructions to run nonexistent commands against today's setup.
Execute in a dedicated temporary workspace with no inherited personal secrets.
A01 must land before broad test runs; merely adding `--offline` to a new CLI
does not protect the old destructive fixtures.

```sh
uv sync --locked --extra dev
uv run ruff check .
uv run pytest
uv build
```

The build/entry point and locked dependencies must be added in A03. CI must
also install the resulting package in a clean environment outside the checkout;
`uv build` alone is not proof that the installed application works.

After the relevant command handlers exist, verify the public workflow in an
isolated, explicitly selected data directory:

```sh
uv run nyc-housing --data-dir <test-workspace> setup
uv run nyc-housing --data-dir <test-workspace> doctor --json
uv run nyc-housing --data-dir <test-workspace> corpus install core --text-only
uv run nyc-housing --data-dir <test-workspace> corpus verify
uv run nyc-housing --data-dir <test-workspace> evaluate --offline
uv run nyc-housing --data-dir <test-workspace> serve --no-browser
```

`<test-workspace>` is a placeholder, not a literal path. CI uses synthetic/local
artifact fixtures so routine tests do not fetch official sources. The real
source-install variant is a separate explicitly selected contract test.
For paid tests, use a test credential and an explicit `--max-cost-usd` ceiling;
do not put the key on the command line. Ordinary `pytest` excludes live/paid
markers and denies unexpected outbound traffic.

### 10.3 Release evidence records

Create evidence under a proposed `docs/Release_Readiness/` directory, with large
or restricted artifacts retained outside Git and referenced by hash:

- Baseline/comparator revision, corpus/source/profile manifests, and active counts.
- Migration round-trip and recursive artifact-integrity report.
- Retrieval metrics, exact-case results, and reviewed answer-quality disposition.
- Cross-platform clean-install/browser matrix with exact runtime versions.
- Memory/latency/disk benchmarks and reference hardware/network conditions.
- Security/privacy/cost/fault-recovery suite results and remaining limitations.
- Dated source/API/provider contract checks and bounded evaluation usage.
- License/redistribution decisions, supported profile, and upgrade/rollback matrix.

Record failures and limitations alongside passes. A dependency being unavailable
is not evidence that its test passed; an intentionally reduced scope needs an
explicit specification/release-note change.

## 11. Risk register and decision deadlines

| Risk/dependency | Mitigation | Decision/evidence due |
| --- | --- | --- |
| Existing uncommitted work or applied-but-untracked migrations | Reconcile baseline, preserve revisions, isolate tests before refactor | A01 |
| Legacy configuration leaks into local startup | Explicit local profile/context; hostile environment/import tests | A02 |
| Two databases diverge during crash/upgrade | Single-store activation, idempotent reconciliation, coordinated backup/schema preflight | B01/B02, E01 drills |
| FTS ranking differs from PostgreSQL | Frozen comparable inputs, generation-scoped index, held-out parity tests | B05 before default switch |
| Source access or parser changes break first install | Early bounded contract checks, source anchors, manual import, truthful partial state | B03 and E02 release recheck |
| Imported embeddings lack complete provenance | Verify profile/preprocessing or quarantine; never guess compatibility | B04 |
| Key storage/FTS/numeric packages differ by OS | Early package probes, clear fallback, tested-wheel support matrix | A03/C02, verified E02 |
| Hidden paid callers bypass caps or retries duplicate charges | One gateway, attempt-level events, multi-process tests, uncertainty retention | C03 before any real-provider run |
| Legal reviewer or approved model budget unavailable | Name review owner and designate must-pass suite early; retain source-only internal milestones | Arrange in A01; close before E02 gate |
| HPD API requires token or cannot resolve an address uniquely | Versioned adapter, token support, candidate selection, cache/error distinctions | D01 |
| History leaks through jobs/logs/backups | Memory-only payloads, centralized redaction, archive inspection and expiry tests | C04/E01 |
| Code rollback loses known accounting or references | Preserve ledger events, compatible backups, staged restore, retained artifacts | E01 |
| Missing platform machines make support claims untested | Arrange runners/manual testers early; publish only evidenced platforms | A03 planning, E02 gate |
| Public release lacks license/source permissions or repository destination | Maintainer decision, attribution/release audit, official downloads by default | E03; content permission before any asset publication |
| Bulk data work absorbs the core release | Keep engine/importer/analytics behind X01 and optional extras | Throughout L1 |

No engine benchmark, case-law expansion, Docker polish, or fully local model
selection is allowed to become an accidental dependency of L1. Conversely,
local access, cost accounting, recovery, and legal-quality gates are not optional
just because the program runs on the user's computer.

## 12. Execution and handoff discipline

### 12.1 Unit of implementation

Use a work package as the planning unit, then split it into small reviewable
changes when needed: contract/schema, implementation, integration, and evidence.
Each change should contain its relevant tests and documentation. Keep package
boundaries stable enough that UI and adapter work can proceed against fixtures
once the underlying contracts are agreed, without requiring parallel staffing.

For each completed package, record:

1. Linked specification IDs and the exact behavior delivered.
2. Files/migrations/contracts changed and compatibility implications.
3. Commands/tests executed with environment/network/cost scope.
4. Evidence of acceptance, unresolved issues, and dependent packages now enabled.
5. Recovery/rollback instructions where data or schema behavior changed.

Do not treat a merged scaffold, happy-path demo, or passing fake-provider unit
suite as completion of a feature whose acceptance also requires migration,
browser, live-connector, or domain-review evidence.

### 12.2 Recommended first implementation sequence

Start with **A01**, then **A02**, then **A03**. The first acceptance demonstration
is deliberately small: a fresh local synthetic workspace launches with no remote
credentials while the legacy project remains untouched. Continue through
**B01–B05** to a usable keyless legal corpus before enabling new paid work.

The concrete first change should be the test-environment guard and regression
for an inherited remote `DATABASE_URL`. That removes a known risk from every
subsequent implementation/test step without forcing an early product rewrite.

### 12.3 Final L1 handoff

- Every L1 feature in Section 9 has closing evidence; all specification release
  gates are satisfied or an explicitly approved scope revision is documented.
- The canonical setup succeeds on every advertised platform with no maintainer
  service, database account, object-storage account, or required bulk dataset.
- Current legal coverage and evaluated research usefulness survive the storage
  change, with honest source/freshness/offline limitations.
- Migration and rollback preserve original remote resources and known accounting.
- Release materials are reviewed and ready for publication; no infrastructure
  retirement or public publishing has been assumed.

This plan schedules application implementation only after approval to implement.
Writing or linking it does not execute migrations, incur provider charges,
publish the repository, or alter runtime behavior.
