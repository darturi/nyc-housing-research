# Codebase audit — 19 September 2026

Audited checkout: `1f40d41` (`First pass at adding a large selection of new features`).

## Hardening record — 19 September 2026

The follow-up hardening pass fixes all six P1 findings and the sixteen P2 findings. It also removes the duplicated full-text index described in A23 and consolidates safety-policy ownership for A24. Broader API/CLI decomposition and a reference-aware corpus-history retention policy remain open architectural work; they are not represented as completed.

The original findings are retained below for traceability. This section describes the current changes and why they were made. **38 additional regression cases** were added: 37 in four hardening test modules and one HPD cache-upgrade case. Existing tests were updated where behavior or schema versions intentionally changed. The pre-existing `.gitignore` edit was preserved.

### Fixes and regression coverage

| Finding | Status | Change and reason | Implementation | Regression evidence |
| --- | --- | --- | --- | --- |
| A01 | Fixed | Resolve current source consent when reading cached vectors and recheck selected evidence before prompt construction. Rolling back content cannot restore revoked permission. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/retrieval/local.py:334) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:89) |
| A02 | Fixed | Close SQLite backup connections explicitly, checkpoint sanitization, and serialize a database in DELETE journal mode so excluded session/launcher/lease state cannot survive in a WAL sidecar. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:223) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:44) |
| A03 | Fixed | Share current workspace policy across workers and the gateway; existing connectors consult live network policy. New work sees changed budgets, offline mode, and deadlines without restarting. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/workspace/context.py:72) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:141) |
| A04 | Fixed for supported profiles | Reserve a conservative UTF-8 byte bound plus answer framing and maximum output; reject excessive per-input bounds before transport. Accurate settlement remains intact. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/providers/tokens.py:1) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:207) |
| A05 | Fixed | Declare tzdata as a base runtime dependency and update the lockfile. Default timezone validation succeeds with system timezone lookup disabled. Windows CI now checks each wheel/sdist smoke command exit code. | [Code](/Users/danielarturi/Developer/NYC_Housing/pyproject.toml:8) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:339) |
| A06 | Fixed | Use one crash-released OS maintenance lock; recover a persisted barrier only after acquiring that lock. Temporary-directory failures also release ownership, while live owners remain protected. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/barrier.py:1) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:80) |
| A07 | Fixed | Transactionally retire save receipts and comparison reviews and detach child-item references before deletion. Reusing a deleted save key creates a fresh item; last-matter unlink works. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/research/matters.py:303) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:128) |
| A08 | Fixed | Persist recoverable process ownership on queued and running jobs. A dead process is detected before lease expiry; resumable jobs pause and process-local answers fail with a resubmission instruction. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/jobs/service.py:355) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:289) |
| A09 | Fixed | Recover abandoned reservations as uncertain without releasing possible charges. Expose unresolved attempt IDs and append-only cost corrections through the CLI, including charges from prior months. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/usage/ledger.py:323) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:378) |
| A10 | Fixed | Bind dossier queries to the confirmed building identity; reject mismatched query IDs, mixed records, unrelated candidates, and unresolved selections, including zero-record responses. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/research/dossiers.py:139) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_content.py:86) |
| A11 | Fixed | Compare canonical bundle content independently of acquisition paths/timestamps, retain local consent, and reuse identical chunk/profile vectors. Conflicting canonical records still fail validation. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/corpus/bundle.py:494) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:148) |
| A12 | Fixed | Offload synchronous property lookup/summary, credential checks, dossier lookup, extraction, and retention operations from async handlers so the event loop can serve other requests. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:1300) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:421) |
| A13 | Fixed | Derive a stable CSRF token from the current session secret. Tabs sharing a valid session remain authorized while session expiry, secret rotation, and origin checks remain effective. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/security/local_session.py:141) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:194) |
| A14 | Fixed for supported OOXML blocks | Traverse content controls, custom XML, and inserted/moved-to blocks; omit deleted revisions and warn about unsupported text-bearing block containers instead of silently discarding them. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resource_parsers.py:390) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_content.py:39) |
| A15 | Fixed | Split oversized DOCX paragraphs, cells, and notes with parent locators and offsets; enforce 4,000-character/7,500-byte chunk bounds so later clauses survive indexing and excerpt selection. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resource_parsers.py:353) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_content.py:39) |
| A16 | Fixed | Page dated records followed by a separately ordered undated partition when no date filter is requested. Version the connector/cache identity so old incomplete cached results cannot mask the correction. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/hpd/connector.py:237) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_content.py:114) |
| A17 | Fixed | Remove staged originals/metadata after cancellation or nonretryable failure. Retention removes old orphan/terminal stages while protecting active, paused, and retryable job inputs. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/jobs/resources.py:259) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:202) |
| A18 | Fixed | Include staged originals and metadata referenced by resumable resource jobs in the verified backup manifest. A restored paused import can finish without reuploading. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:116) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:227) |
| A19 | Fixed | Bind approved operation ceilings to every gateway reservation, using the stricter of operation and current workspace policy. Resumed work counts previous settled, reserved, and uncertain spend. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/providers/gateway.py:455) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:242) |
| A20 | Fixed | Stream responses in bounded increments, reject oversized declared lengths early, and stop decoded-body consumption near the artifact cap. Reconstructed responses avoid double decompression. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/corpus/download.py:191) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_content.py:167) |
| A21 | Fixed | Copy application code before installation and install the legacy runtime extra in Docker. CI now builds the image and checks the hosted app import. | [Code](/Users/danielarturi/Developer/NYC_Housing/Dockerfile:8) | Built image; isolated hosted health check returned HTTP 200; CI image smoke check |
| A22 | Fixed | Serialize hosted admission and persist token/cost reservations before releasing the transaction. Count in-flight and uncertain requests in quotas; settle completed work and retain uncertain failures. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/limits/service.py:255) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_legacy.py:26) |
| A23 | Duplication fixed; retention follow-up | Corpus schema 3 indexes each immutable chunk once and joins generation membership at search time. A backed-up migration rebuilds the index; historical searches and rollback retain their evidence. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/corpus/service.py:1015) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:474) |
| A24 | Mitigated; broader refactoring remains | Centralize live policy, OS ownership, maintenance barriers, token bounds, and operation admission around the reproduced failures. Domain-router, typed API model, and shared CLI-command extraction remain follow-up work. | [Code](/Users/danielarturi/Developer/NYC_Housing/app/workspace/context.py:72) | [Regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_safety.py:141) |

Additional lifecycle checks cover a live provider call outlasting its lease, a crashed maintenance process, failed backup setup, startup ownership, and migration refusal while a launcher holds the workspace. The [HPD cache regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hpd_cache.py:100) verifies that the connector upgrade does not reuse a prior `verified_zero` cache entry when undated violations exist. The [schema migration regression](/Users/danielarturi/Developer/NYC_Housing/tests/test_hardening_lifecycle.py:286) checks the pre-upgrade backup and shared-index rebuild.

### Verification after hardening

| Check | Result |
| --- | --- |
| Full suite, including opt-in browser journey with installed Chrome | **393 passed, 2 skipped**, 26.59 seconds; skips were the PostgreSQL variants awaiting a service |
| Hosted concurrent admission against an isolated temporary PostgreSQL 16 cluster and SQLite | **4 passed**; includes both skipped PostgreSQL cases, for **395 distinct passing tests** across runs |
| `ruff check --no-cache .` and `git diff --check` | Passed |
| 10,000 chunks, 1,536-dimensional vectors, 20-run retrieval benchmark | Passed targets: startup 0.003 s; warm keyword p95 0.103 s; warm hybrid p95 0.167 s; synthetic corpus database 92.5 MB |
| Wheel and source distribution build and distribution-content audit | Passed; artifacts created outside the checkout |
| Base wheel installed in a clean temporary environment outside the checkout | Setup, schema preflight, status, and timezone validation passed with system timezone lookup disabled |
| Docker image build, actual default-command startup, and hosted health check | Passed; health HTTP 200 with an in-memory database and container networking disabled; temporary container stopped afterward |
| Timezone fallback | Default settings validated in a subprocess with `PYTHONTZPATH=''` |

All provider, publisher, and private-resource regression scenarios used fake providers, local fixtures, or mock transports. No paid requests or live private-document uploads were performed. The PostgreSQL cluster was isolated, stopped, and removed after the test. The container health check used no production database. The benchmark measures synthetic local retrieval, not remote provider latency or production-scale historical storage. Existing FastAPI/Starlette/websockets deprecation warnings remain; no new test failures remain.

### Upgrade and operational changes

- **Existing workspaces require corpus schema 3.** Close the application, then run `nyc-housing --data-dir /path/to/workspace migrate preflight` followed by `nyc-housing --data-dir /path/to/workspace migrate apply`. Migration creates a pre-upgrade ZIP, takes the launcher/maintenance locks, rebuilds shared FTS rows, and preserves corpus history. State schema remains version 2. This hardening task did not migrate the user's actual workspace.
- **Budgets now reserve conservatively.** UTF-8 byte bounds can reject a request that a precise tokenizer could fit. Explicit operation ceilings remain binding across resumed batches. Unknown-price manual requests retain their explicit outside-budget treatment. Configured prices and tokenization assumptions cannot guarantee an arbitrary custom endpoint's invoice.
- **Recover uncertain charges deliberately.** Run `nyc-housing usage --attempts --json`, verify the provider's billing record, then use `nyc-housing usage --reconcile ATTEMPT_ID --actual-usd AMOUNT --reason "Verified provider receipt" --json`. Corrections append to history. Dead-process recovery never silently assumes the request was free. See [cost and reconciliation documentation](/Users/danielarturi/Developer/NYC_Housing/docs/Costs.md).
- **HPD cache format advances to `hpd-soda21-v3`.** Unfiltered searches include undated records. An old cache entry no longer satisfies a current search; a fresh online lookup may be needed. Explicit date filters intentionally exclude records whose dates cannot satisfy the filter.
- **Saved-item deletion retires idempotency receipts.** Reusing that key after deletion creates a new saved item. Retryable imports retain staged private inputs for resumption; cancellation/nonretryable completion removes them, and maintenance handles old orphans.

### Remaining scope and limitations

1. **A23:** Generation membership, source versions, and original artifacts remain retained for rollback and saved research. Shared FTS removes repeated body indexing but does not implement automatic history deletion, database shrinking, or a corpus storage quota. Reference-aware retention needs a separate design and migration.
2. **A24:** API/CLI modules still contain substantial transport and orchestration code. This pass extracts the safety-critical shared responsibilities; it does not claim complete domain-router or typed-command decomposition.
3. Native Windows startup and a full signed/notarized desktop build were not run locally. Existing cross-platform bootstrap/distribution CI remains responsible for these release checks. No live publisher contract, real model billing, or document-format completeness claim follows from fixture tests; unsupported OOXML constructs may still require future support.
4. Streaming downloads enforce the artifact cap but still buffer an accepted artifact. Backup/bundle creation can retain multiple archive members in memory; large-workspace memory profiling and streaming archive construction remain follow-up work beyond the reproduced A20 download defect.

## Original assessment — before hardening

The application has useful foundations: explicit source provenance, immutable corpus generations, transactional local storage, a centralized provider gateway, spend reservations, and a substantial test suite. The most consequential defects are at the boundaries between these systems. Consent is cached with corpus content, worker configuration outlives settings changes, recovery does not cover all persisted states, and backup serialization does not reliably capture sanitization.

This audit identifies **24 findings: 6 P1, 16 P2, and 2 P3**. Address the P1 findings before relying on the application for private documents, enforced spending limits, or recoverable backups. Two P2 findings concern the optional legacy hosted application and do not affect the default local application.

The initial audit was read-only except for this document. The subsequent, explicitly requested hardening pass is recorded above. The pre-existing modification to `.gitignore` remains untouched.

### Priority and evidence definitions

- **P1:** High impact; privacy, spending controls, recovery, or a supported installation path fails.
- **P2:** Actionable correctness, reliability, completeness, or maintainability defect.
- **P3:** Architectural improvement with longer-term operational consequences.
- **Reproduced:** Observed in an isolated temporary workspace, using fixtures or mocked providers where appropriate.
- **Code-confirmed:** The implementation establishes the failure mechanism; the relevant external system or production-scale condition was not exercised.
- **Architectural:** A design limitation, distinguished from an observed runtime failure.

## Original audit scope and validation

The review covered the local API and browser interface; CLI and launchers; desktop packaging; workspace configuration; authentication and CSRF; SQLite schemas and migrations; source acquisition, resource extraction, generation publication, and bundles; retrieval and answer generation; provider credentials, profiles, and accounting; job admission, cancellation, and recovery; HPD search, caching, exports, and dossiers; saved research; backup and retention; the legacy hosted application, ORM models, and PostgreSQL migrations; distribution files; and CI/tests. Review depth followed data flow and failure boundaries. This was not a formal proof or a claim that every execution path is defect-free.

| Validation | Result |
| --- | --- |
| Full pytest invocation, without pytest cache or bytecode writes | 353 passed, 1 skipped, 3 failures caused by sandbox restrictions on binding localhost sockets |
| The three affected launcher tests, rerun with localhost access | 3 passed |
| Opt-in browser journey, using the already installed Google Chrome | 1 passed |
| Distinct tests verified across these runs | **357 passed** |
| `ruff check --no-cache .` | Passed |
| Focused audit probes | Reproduced consent leakage into a gateway prompt, unsanitized backup state, stale worker settings, cap overrun accounting, saved-item deletion failures, recovery gaps, bundle conflicts, dossier identity mismatch, CSRF invalidation, DOCX extraction defects, and staging-file lifecycle failures |

The browser test initially lacked Playwright's downloaded Chromium; it subsequently passed with `PLAYWRIGHT_CHROMIUM_EXECUTABLE` pointing to the installed Chrome. No browser installation was needed. The initial socket failures and missing test browser are environment conditions, not product findings.

Tests and probes used temporary workspaces and fake providers or `httpx.MockTransport`. No paid model requests were made and no live private resources were uploaded. Native Windows startup, a complete signed/notarized macOS build, real PostgreSQL/S3 behavior, and live publisher/provider contracts were not exercised. Windows timezone behavior was simulated by removing access to system timezone data. Installed-package vulnerability scanning and independent legal-content validation were outside this audit.

## Findings index

| ID | Priority | Finding | Evidence |
| --- | --- | --- | --- |
| A01 | P1 | Revoked model consent can reappear through the vector cache after rollback | Reproduced |
| A02 | P1 | Backup archives can omit sanitization committed to SQLite WAL | Reproduced |
| A03 | P1 | Newly submitted jobs use stale offline and budget settings | Reproduced |
| A04 | P1 | Token estimates cannot enforce the advertised hard spending caps | Reproduced with mocked usage |
| A05 | P1 | Clean Windows installation lacks a required timezone dependency | Simulated and code-confirmed |
| A06 | P1 | An abandoned maintenance barrier permanently blocks work | Reproduced |
| A07 | P2 | Saved-item deletion and final unlink fail on foreign keys | Reproduced |
| A08 | P2 | Immediate restart leaves running and queued jobs stranded | Reproduced |
| A09 | P2 | Crashed spend reservations remain charged against capacity indefinitely | Reproduced |
| A10 | P2 | A dossier can accept another building's empty or mixed result | Empty-result case reproduced |
| A11 | P2 | Bundle import rejects legitimate overlapping source versions | Reproduced |
| A12 | P2 | Synchronous network work blocks the async API event loop | Code-confirmed |
| A13 | P2 | Opening another tab invalidates the first tab's CSRF token | Reproduced |
| A14 | P2 | DOCX content controls silently lose document text | Reproduced |
| A15 | P2 | DOCX chunks bypass normal size limits | Reproduced |
| A16 | P2 | HPD results silently exclude undated records while claiming completeness | Code-confirmed |
| A17 | P2 | Failed resource imports leave private staging files indefinitely | Reproduced |
| A18 | P2 | Backups preserve resumable import jobs but omit their input files | Reproduced |
| A19 | P2 | Explicit operation cost ceilings are not bound to gateway admission | Code-confirmed |
| A20 | P2 | Download limits are checked after the entire body is allocated | Code-confirmed |
| A21 | P2 | Docker starts the legacy app without its required dependencies | Code-confirmed; legacy only |
| A22 | P2 | Legacy spending limits omit in-flight reservations | Code-confirmed; legacy only |
| A23 | P3 | Every retained generation duplicates the full-text index without reclamation | Architectural |
| A24 | P3 | API/CLI orchestration and mutable policy ownership need clearer boundaries | Architectural |

## High-priority findings

### A01 — Revoked model consent can reappear through the vector cache after rollback

**Locations:** [vector cache and filtering](/Users/danielarturi/Developer/NYC_Housing/app/retrieval/local.py:356), [cached consent field](/Users/danielarturi/Developer/NYC_Housing/app/retrieval/local.py:475), [resource consent mutation](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resources.py:260), [answer evidence retrieval](/Users/danielarturi/Developer/NYC_Housing/app/answer/local.py:180).

The vector cache is keyed by engine identity, generation ID, and embedding profile. Its cached rows include mutable `source_modules.model_use_allowed`. Semantic filtering subsequently trusts that cached value. Disabling model use updates the source module and publishes a generation, but reactivating an older indexed generation can reuse a cache populated when consent was enabled. Answer construction does not recheck current consent before passing the selected excerpts to the gateway.

**Reproduction:** Create two user resources with model use enabled; index them with `fake-small-16`; ask a question across user resources to warm the cache; disable model use for one resource; roll back; ask again. The second resource keeps semantic retrieval eligible. The probe observed `model_use_allowed=False` in the resource service while the disabled resource's distinctive private text was present in the next `ProviderGateway.answer` prompt. The gateway was fake, so this demonstrated the outbound prompt boundary without transmitting data externally.

**Impact:** A user can revoke permission and still have that document selected for model processing. This is a privacy boundary failure, not just stale display metadata.

**Recommended correction:** Cache immutable vectors and content separately from mutable authorization. Resolve consent against current state on each retrieval and recheck it immediately before provider submission. Invalidate relevant cached metadata on policy changes and generation activation, but do not rely on invalidation alone as the permission check.

**Regression check:** Warm cache → revoke consent → rollback/reactivate → answer across mixed permitted and prohibited sources. Assert that prohibited text reaches neither query evidence nor the provider prompt.

### A02 — Backup archives can omit sanitization committed to SQLite WAL

**Locations:** [copy, sanitize, then read the database file](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:56), [sanitization and SQLite connection lifecycle](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:225), [WAL configuration](/Users/danielarturi/Developer/NYC_Housing/app/storage/database.py:143).

`_sanitize_state_copy` uses `with sqlite3.connect(...)` as though exiting the context closes the connection. It commits or rolls back the transaction but does not deterministically close the connection. With WAL mode retained in the copied database, sanitization and `VACUUM` can remain in the sidecar WAL. `create` then archives only `state_copy.read_bytes()`, without checkpointing or including that WAL. Garbage collection is not a reliable synchronization mechanism.

**Reproduction:** In three independent ordinary backup/restore trials, the archived database contained `maintenance_state.active=1` and one `launcher_tokens` row, despite sanitization explicitly clearing both. Creating a new job in each restored workspace failed with `Workspace maintenance is active; no job was admitted or resumed.` No forced garbage-collector behavior was used.

**Impact:** A successfully reported backup can restore into a workspace that cannot start work. Session/launcher metadata and cache rows intended to be removed can also survive in the archived state. This probe does not imply plaintext API credentials were included; the separate credential-file exclusion still applies.

**Recommended correction:** Explicitly close SQLite connections, checkpoint writes to the main database before serialization, and verify the exact database bytes being archived. A temporary database deliberately converted to a non-WAL journal mode is another viable approach. Include sanitization assertions in archive verification rather than checking only the live temporary connection.

**Regression check:** Open the database extracted from the final ZIP and assert an inactive maintenance barrier, empty excluded tables, and cache behavior matching the chosen option. Restore it, start a new job, and create another backup. Existing tests check restored search and usage, which do not exercise this failure.

### A03 — Newly submitted jobs use stale offline and budget settings

**Locations:** [runners capture the startup context](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:111), [settings replacement](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:569), [worker context ownership](/Users/danielarturi/Developer/NYC_Housing/app/jobs/interactive.py:34), [UI restart notice](/Users/danielarturi/Developer/NYC_Housing/app/static/local.js:1680).

The settings endpoint replaces the closure's `context` and `app.state.workspace`, but existing runner objects retain the original immutable `WorkspaceContext`. This affects newly submitted work as well as already running work. The UI asks the user to restart before provider work, and the response includes `restart_required_for_active_jobs`; neither prevents submission under the obsolete settings.

**Reproduction:** Save `offline=True` and `monthly_budget_usd="0"`, then submit a new answer through the API. A mocked answer service captured `offline=False` and a monthly budget of `15` in the new worker, while the settings response reported the saved values.

**Impact:** Offline mode and reduced spending limits are not authoritative until restart, even though settings save successfully. Profile, endpoint, language, and deadline configuration can also diverge between direct routes and background jobs.

**Recommended correction:** Give job admission and gateway dispatch a shared current-policy source. Define which job inputs are intentionally snapshotted and which safety limits apply immediately. If restart remains necessary, enforce that requirement by blocking affected submissions.

**Regression check:** Change offline mode, budget, and deadline while the app remains open; verify subsequent answer, indexing, and property-export jobs use the new policy.

### A04 — Token estimates cannot enforce the advertised hard spending caps

**Locations:** [answer reservation](/Users/danielarturi/Developer/NYC_Housing/app/providers/gateway.py:90), [bytes-divided-by-four estimate](/Users/danielarturi/Developer/NYC_Housing/app/providers/gateway.py:517), [reservation admission](/Users/danielarturi/Developer/NYC_Housing/app/usage/ledger.py:126), [settlement](/Users/danielarturi/Developer/NYC_Housing/app/usage/ledger.py:174).

Gateway admission treats `ceil(UTF-8 bytes / 4)` as the input-token reservation. This is an estimate, not an upper bound. Reserving the maximum output tokens does not compensate for underestimated input tokens. Settlement accepts the provider's larger actual amount, so a request admitted under both caps can settle above both caps. The same estimate is used for embedding work.

**Reproduction:** With both caps set to `$0.00146`, a 400-character input reserved 100 input tokens plus the profile's 1,200 output tokens. A mocked successful response reporting 400 input tokens and 1,200 output tokens settled `$0.00152000`. These figures use repository profile prices and synthetic usage; they are not a claim about current vendor billing or tokenization of that particular string.

**Impact:** The local budget is a best-effort estimate despite behaving and being presented as a hard admission control. Multiple in-flight underestimates compound the discrepancy.

**Recommended correction:** Use the appropriate tokenizer or a documented conservative upper bound, account for protocol/model overhead, and enforce profile input limits before submission. Keep actual settlement accurate; rejecting or clamping a bill after the request cannot undo the charge. Clearly distinguish estimates from any remaining enforceable guarantee.

**Regression check:** Near-cap inputs with high token density, multiple concurrent calls, and embedding batches; confirm reserved maximum cost covers allowed provider usage before any network submission.

### A05 — Clean Windows installation lacks a required timezone dependency

**Locations:** [base dependencies](/Users/danielarturi/Developer/NYC_Housing/pyproject.toml:8), [timezone validation](/Users/danielarturi/Developer/NYC_Housing/app/workspace/settings.py:91), [Windows bootstrap dependencies](/Users/danielarturi/Developer/NYC_Housing/start.ps1:57), [optional transitive timezone dependency](/Users/danielarturi/Developer/NYC_Housing/uv.lock:604).

Default settings unconditionally construct `ZoneInfo("America/New_York")`. The base and credentials installations do not declare `tzdata`. In the lockfile, it is pulled in on Windows through `psycopg`, which belongs to the legacy/dev extras. The normal launcher installs the credentials extra. A Windows machine without an IANA timezone database therefore lacks a required runtime input.

**Reproduction:** Running default settings validation with `PYTHONTZPATH=''` in the current environment, which lacks the `tzdata` package, raised `ModuleNotFoundError: tzdata`, then `ZoneInfoNotFoundError`, then `LocalSettingsError: budget_timezone is not recognized.` This simulates missing timezone data; it is not a native Windows execution result.

**Impact:** Fresh Windows setup can fail before the application opens. A dev installation can hide the missing dependency. The existing clean-bootstrap CI job should expose it if executed in the corresponding environment; the workflow's existence is not evidence that it currently passes.

**Recommended correction:** Declare `tzdata` as a direct runtime dependency on platforms that need it, or ship an equally explicit timezone-data solution.

**Regression check:** Validate the default settings and run setup from a clean base wheel and credentials-only environment with no system timezone files.

### A06 — An abandoned maintenance barrier permanently blocks work

**Locations:** [backup barrier](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:99), [backup cleanup scope](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:56), [retention barrier](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/retention.py:342), [admission check](/Users/danielarturi/Developer/NYC_Housing/app/jobs/service.py:438).

Maintenance persists a boolean barrier without an owner identity or expiring/recoverable lease. Normal `finally` cleanup clears it, but startup does not recover it after process termination. There is also an ordinary exception gap: backup creates its temporary directory after entering the barrier and before entering its `try/finally`, so temporary-directory creation failure can strand the barrier without a crash.

**Reproduction:** Enter a maintenance barrier, close the storage/process boundary without leaving it, reopen the workspace, and create a job. Even a barrier dated three days earlier blocks admission. No supported stale-barrier recovery path was found.

**Impact:** Jobs, paid-call admission, further backups, and retention remain blocked after a maintenance interruption. Restarting alone does not repair the workspace. This is independent of A02, which can introduce an active barrier through a normal restore.

**Recommended correction:** Tie maintenance to a recoverable owner/lease and a workspace process lock. Recover only when the owner is demonstrably gone. Cover all setup work after acquisition with cleanup and provide an explicit recovery operation with diagnostics.

**Regression check:** Terminate backup/prune after barrier acquisition; restart and recover safely. Also force temporary-directory creation to fail and verify the barrier is released.

## Correctness and reliability findings

### A07 — Saved-item deletion and final unlink fail on foreign keys

**Locations:** [receipt creation](/Users/danielarturi/Developer/NYC_Housing/app/research/matters.py:266), [item deletion](/Users/danielarturi/Developer/NYC_Housing/app/research/matters.py:302), [restrictive receipt foreign key](/Users/danielarturi/Developer/NYC_Housing/app/storage/schema.py:446).

Every normal `save_payload` inserts a `save_receipts` row referencing the saved item. That foreign key has no deletion action, while `delete_item` deletes the saved item without first handling its receipts. Unlike matter links and notes, the receipt does not cascade.

**Evidence and impact:** Saving an answer and then calling either `delete_item(id, apply=True)` or unlinking it from its only matter produced `sqlite3.IntegrityError: FOREIGN KEY constraint failed`. Ordinary deletion is broken, and the API's domain-error handling does not turn this integrity exception into a useful user response.

**Recommended correction:** Define receipt retention/idempotency semantics after deletion and implement them transactionally, through an appropriate cascade, tombstone, or explicit cleanup. Check other item references, including comparison reviews, at the same time.

**Regression check:** Delete a normally saved item, unlink its last matter, delete an item with notes/reviews, and retry an old save idempotency key afterward.

### A08 — Immediate restart leaves running and queued jobs stranded

**Locations:** [recovery selection](/Users/danielarturi/Developer/NYC_Housing/app/jobs/service.py:350), [startup-only recovery](/Users/danielarturi/Developer/NYC_Housing/app/jobs/interactive.py:45), [resource runner initialization](/Users/danielarturi/Developer/NYC_Housing/app/jobs/resources.py:46).

Recovery considers only `running`/`cancel_requested` jobs whose leases have already expired. Runner constructors invoke it once. Restarting before the old lease expires leaves those jobs running, and no periodic recovery sweep transitions them after expiry. Persisted `queued` jobs are omitted altogether and are not automatically rescheduled into the new executor.

**Evidence and impact:** A job claimed by a dead worker with a 300-second lease and a separate queued answer both remained unchanged after immediate recovery; the recovery count was zero. Such rows can block their target or leave status polling stuck. A queued job may still be manually cancellable, but it is not automatically recovered.

**Recommended correction:** Track process ownership, recover abandoned work under the workspace lock, and define startup handling for queued jobs. Requeue only jobs whose durable inputs exist; fail process-local interactive work with a clear resubmission instruction. Add safe periodic lease recovery where needed.

**Regression check:** Crash before claim, immediately after claim, and during cancellation; restart both before and after expiry and verify no ownerless active states remain indefinitely.

### A09 — Crashed spend reservations remain charged against capacity indefinitely

**Locations:** [prior-month reservations count against admission](/Users/danielarturi/Developer/NYC_Housing/app/usage/ledger.py:108), [reconciliation accepts only uncertain attempts](/Users/danielarturi/Developer/NYC_Housing/app/usage/ledger.py:264), [job recovery](/Users/danielarturi/Developer/NYC_Housing/app/jobs/service.py:350).

An abrupt exit after reservation can leave a usage attempt permanently `reserved`. Expiring paid-call concurrency leases does not transition its ledger state. Job recovery does not reconcile the attempt either. Old reserved amounts deliberately continue to reduce later months' capacity, while `correct_uncertain` rejects reservations that have never been transitioned to `uncertain`. No exposed reconciliation workflow was found for this case.

**Evidence and impact:** A January reservation still appeared as reserved in the September summary, and attempting correction raised `Only an uncertain provider attempt can be corrected.` Repeated interrupted calls can consume usable budget indefinitely without corresponding known charges.

**Recommended correction:** Recover ownerless in-flight attempts as uncertain, preserve the audit trail, and expose a supported reconciliation path. Do not simply release them at zero: a request might have reached the provider before the crash.

**Regression check:** Interrupt before dispatch, after dispatch, and before settlement; restart and reconcile each state, including across a month boundary.

### A10 — A dossier can accept another building's empty or mixed result

**Locations:** [dossier identity check](/Users/danielarturi/Developer/NYC_Housing/app/research/dossiers.py:152), [caller-supplied dossier query](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:1944).

`create_observation` checks identity only if the returned records contain building IDs, and then requires merely that the expected building occur somewhere in the set. It does not validate an empty result's query identity, require every record to match, or reject unresolved selection. The route accepts a query independently of its `identity_id`.

**Evidence and impact:** An identity for building `111` accepted a complete, zero-record response whose query and candidate were building `222`. The saved dossier identified building `111` and marked the HPD panel complete. Mixed-building acceptance follows directly from the membership check. A clean bill of records or unrelated violations can therefore be attached to the wrong property.

**Recommended correction:** Construct the query from the confirmed server-side identity, require a resolved result, validate every returned record, and bind zero-result observations to the exact confirmed query identity.

**Regression check:** Wrong-building zero result, mixed records, unresolved candidates, and correctly matched zero result.

### A11 — Bundle import rejects legitimate overlapping source versions

**Location:** [record comparison and embedding insertion](/Users/danielarturi/Developer/NYC_Housing/app/corpus/bundle.py:494).

Import rewrites each incoming source version's `artifact_uri` to the bundle artifact location, then compares almost every field with an existing row of the same ID. A source installed through ordinary acquisition has a different local artifact path even when the underlying version/content is identical. Shared embeddings are also inserted unconditionally, presenting another conflict when importing overlapping indexed generations.

**Evidence and impact:** Two fresh workspaces independently installed the same RPAPL fixture. Exporting one and importing into the other failed with `Bundle record conflicts with local source_versions`. Generation IDs differed, so this was not the intentional same-generation rejection. Bundles cannot reliably extend or update an already populated workspace with shared sources.

**Recommended correction:** Separate canonical version identity from machine-local storage paths and acquisition metadata. Reuse equivalent existing records and embeddings after validating hashes/profile identity; reject true content conflicts.

**Regression check:** Managed installation followed by a partially overlapping bundle, a later bundle sharing unchanged versions, and two indexed generations sharing vectors.

### A12 — Synchronous network work blocks the async API event loop

**Locations:** [property search](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:1280), [property summary](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:1309), [dossier creation](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:1944).

Several `async def` endpoints await body parsing and then call synchronous services directly. These services use synchronous `httpx.Client` requests and blocking database/file operations. Unlike ordinary `def` FastAPI endpoints, these calls are not automatically moved to the thread pool.

**Impact:** An HPD lookup or model summary can occupy the server event loop for its network timeout. Other UI requests, progress streams, and cancellation requests then stall despite the application's background-job design. The mechanism is code-confirmed; no production latency benchmark was performed.

**Recommended correction:** Use typed synchronous endpoints where appropriate, explicitly offload synchronous service calls, or route long operations through the existing job system. Preserve deadlines and cancellation across the boundary.

**Regression check:** Hold a mocked property/provider request open while concurrently fetching job status and cancelling a different job; those requests should remain responsive.

### A13 — Opening another tab invalidates the first tab's CSRF token

**Locations:** [single stored CSRF hash](/Users/danielarturi/Developer/NYC_Housing/app/security/local_session.py:141), [CSRF refresh route](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:289), [browser reconnection](/Users/danielarturi/Developer/NYC_Housing/app/static/local.js:109).

Every reconnect rotates the one CSRF hash associated with the shared session cookie. Each browser tab retains its own token in memory. Loading or refreshing a second tab invalidates the first tab's token, and the generic request helper has no recovery for this condition.

**Evidence and impact:** After obtaining a session and token, calling `/api/v1/session/csrf` and then PATCHing settings with the original token returned HTTP 401. Normal multi-tab usage causes apparently random save/action failures.

**Recommended correction:** Support multiple valid tab tokens or a stable session-bound CSRF mechanism, with expiry and origin checks intact. Avoid silently retrying non-idempotent mutations unless their outcome is known.

**Regression check:** Two tabs share a cookie, reconnect independently, and both perform valid mutations.

### A14 — DOCX content controls silently lose document text

**Location:** [top-level DOCX body traversal](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resource_parsers.py:249).

The parser processes direct body children only when they are paragraphs or tables. Common block wrappers such as `w:sdt` / `w:sdtContent` are not traversed. A document with ordinary text plus a substantive clause inside a content control can import successfully while losing the controlled clause, without an omission warning.

**Evidence and impact:** A fixture containing `Ordinary text.` and `Important controlled clause.` inside a content control yielded only `Ordinary text.` with an empty warning tuple. Search and answers can silently omit important content from an otherwise usable imported document.

**Recommended correction:** Traverse supported OOXML block containers recursively while retaining structural locators. Emit explicit warnings for unsupported constructs containing text. Apply the same review to block-level tracked revisions and custom XML wrappers.

**Regression check:** Mixed ordinary and controlled paragraphs/tables, nested controls, and block-level revisions; verify extracted content and warnings.

### A15 — DOCX chunks bypass normal size limits

**Locations:** [paragraph chunks](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resource_parsers.py:267), [table-cell chunks](/Users/danielarturi/Developer/NYC_Housing/app/corpus/resource_parsers.py:293), [answer excerpt truncation](/Users/danielarturi/Developer/NYC_Housing/app/answer/local.py:507), [profile token limit](/Users/danielarturi/Developer/NYC_Housing/app/providers/profiles.py:129).

DOCX paragraphs, cells, and notes are each emitted as a single chunk, bypassing the bounded `_chunk_text` path used elsewhere. Whole-document character and chunk-count checks do not limit each chunk. Gateway dispatch does not enforce `max_input_tokens`.

**Evidence and impact:** A 40,000-character paragraph imported as one 40,000-character chunk. Long chunks can exceed embedding input limits, and answer evidence takes only their first 4,000 characters: a match later in the paragraph can retrieve a chunk whose relevant passage never appears in the prompt.

**Recommended correction:** Apply bounded, preferably token-aware splitting to all DOCX structural elements while retaining parent locators and offsets. Validate provider input sizes before dispatch and select query-relevant excerpts rather than blindly taking the prefix.

**Regression check:** Long paragraphs, table cells, and notes with the decisive search term beyond character 4,000; verify bounded indexing input and relevant cited evidence.

### A16 — HPD results silently exclude undated records while claiming completeness

**Locations:** [unconditional inspection-date filter](/Users/danielarturi/Developer/NYC_Housing/app/hpd/connector.py:247), [completeness and zero-result status](/Users/danielarturi/Developer/NYC_Housing/app/hpd/connector.py:269).

Every records query adds `inspectiondate is not null`, including queries without a user-selected date constraint. Pagination then labels exhaustion of this restricted set as complete; an empty set becomes `verified_zero`. The response does not disclose the hidden exclusion.

**Impact:** If the publisher has otherwise matching rows with missing inspection dates, complete exports and dossiers omit them and can overstate a zero-result conclusion. This finding establishes the handling defect; the audit did not measure how many such rows exist in the live dataset.

**Recommended correction:** Include a separately paginated null-date partition with deterministic ordering, or explicitly represent and display the restricted coverage. Do not describe an incomplete partition as the entire property's result.

**Regression check:** Fixtures with dated rows, undated rows, and only undated rows; ensure no silent omissions and truthful completeness metadata.

### A17 — Failed resource imports leave private staging files indefinitely

**Locations:** [staging before import](/Users/danielarturi/Developer/NYC_Housing/app/jobs/resources.py:60), [failure handling](/Users/danielarturi/Developer/NYC_Housing/app/jobs/resources.py:230), [retention scope](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/retention.py:112).

Resource jobs write raw input and metadata to `artifacts/resource-staging`. Success and one cancellation path clean up the files, but terminal validation failure does not. Cancelling before the worker claims the job also lacks a reliable stage cleanup path. General retention handles jobs, logs, usage, and property cache, not these staging files.

**Evidence and impact:** Importing a private unsupported file ended in `failed` state while both its `.bin` and `.json` staging files remained. The document never became a resource the user could manage, yet its raw content remains on disk. Pruning its job metadata can make the orphan harder to identify.

**Recommended correction:** Delete staging inputs on non-resumable terminal outcomes. Keep them only for explicitly resumable jobs with a defined retention policy. Reconcile orphan staging files against job references during maintenance.

**Regression check:** Unsupported input, cancelled-before-claim, retryable failure, successful retry, and job retention; verify the appropriate file lifecycle for each.

### A18 — Backups preserve resumable import jobs but omit their input files

**Locations:** [paused jobs allowed during backup](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:108), [artifact enumeration](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/backup.py:137), [resource resume input](/Users/danielarturi/Developer/NYC_Housing/app/jobs/resources.py:271).

Backup includes the state database and allows paused jobs, but artifact enumeration covers source-version originals and optional property cache only. A paused resource-import job still references its staging ID, while its `.bin` and `.json` inputs are omitted from the archive.

**Evidence and impact:** Pause an import through interrupted-job recovery, back up, restore, and resume. After clearing the independently reproduced A02 maintenance barrier in the temporary restored fixture, the job failed with `Resource staging files are unavailable.` Restored resumable work cannot actually resume.

**Recommended correction:** Include referenced durable job inputs in the backup manifest and validate them during restore, or explicitly mark/exclude unsupported pending jobs with a clear user-facing limitation. The restored state must not advertise resumability without the necessary inputs.

**Regression check:** Back up and restore a paused add and replacement import, then complete each without reuploading its file.

### A19 — Explicit operation cost ceilings are not bound to gateway admission

**Locations:** [indexing gateway construction](/Users/danielarturi/Developer/NYC_Housing/app/jobs/maintenance.py:350), [credential validation](/Users/danielarturi/Developer/NYC_Housing/app/providers/validation.py:105), [profile compatibility check](/Users/danielarturi/Developer/NYC_Housing/app/providers/validation.py:193), [gateway uses workspace cap](/Users/danielarturi/Developer/NYC_Housing/app/providers/gateway.py:431).

These operations compare an initial estimate with `max_cost_usd`, then construct a gateway with the ordinary workspace context. Actual reservations therefore enforce the workspace per-operation cap, not necessarily the smaller ceiling explicitly approved for that action. Indexing resume also compares the remaining estimate with the original ceiling without binding previous settled/uncertain spend to that approved limit. The answer-evaluation path already demonstrates a tighter bounded context.

**Impact:** Estimate error, partially completed work, or repeated attempts can exceed a specific approved ceiling while remaining below the wider workspace cap. This is separate from A04: even a better estimator needs the correct cap and accumulated operation spend at admission.

**Recommended correction:** Pass the approved ceiling as an immutable operation-level admission constraint, use the stricter of it and current workspace policy, and include prior settled, reserved, and uncertain attempts for the same operation.

**Regression check:** A ceiling lower than the workspace cap, followed by a resumed multi-batch indexing job with prior charges; the next batch must be denied when it would exceed the approved total.

### A20 — Download limits are checked after the entire body is allocated

**Location:** [source download body buffering](/Users/danielarturi/Developer/NYC_Housing/app/corpus/download.py:191).

`client.get` reads the complete response before `len(response.content)` is compared with `MAX_ARTIFACT_BYTES`. The nominal 250 MB limit consequently does not bound download traffic or peak memory for an oversized response. A timeout also does not establish a total byte limit for a body that keeps arriving.

**Impact:** An unexpectedly large publisher response can consume memory well beyond the supported artifact size before rejection and can terminate the local process. No deliberately oversized live request was made during the audit.

**Recommended correction:** Stream into a bounded temporary artifact, stop as soon as the byte limit is exceeded, and validate a declared content length early without trusting it as the sole check. Carry deadline/cancellation through the read loop. Apply the same bounded-memory review to backup/bundle code that retains all members in memory.

**Regression check:** A streaming mock without `Content-Length` that exceeds the cap; assert consumption stops near the limit rather than reading the remaining body.

## Legacy hosted application findings

These findings apply to the optional `app.main` deployment, rather than the default local launcher. The repository describes the hosted path as legacy; decide explicitly whether it remains runnable or should be retired.

### A21 — Docker starts the legacy app without its required dependencies

**Locations:** [Docker installation and startup](/Users/danielarturi/Developer/NYC_Housing/Dockerfile:8), [optional legacy dependencies](/Users/danielarturi/Developer/NYC_Housing/pyproject.toml:34), [unconditional Argon2 import](/Users/danielarturi/Developer/NYC_Housing/app/auth/password.py:1).

The Dockerfile installs the base project and starts `uvicorn app.main:app`. The hosted application's Argon2 and PostgreSQL dependencies moved to the `legacy` extra, which the Dockerfile does not install. A clean image lacks `argon2-cffi`, and the configured PostgreSQL path also needs `psycopg`.

**Impact:** The checked-in Docker path cannot reliably boot the application it selects. A dev environment masks the problem by installing both dependencies. This conclusion follows from dependency/import paths; a Docker image was not built during this audit.

**Recommended correction:** If supported, install the correct locked runtime extras and smoke-test the actual image command. Otherwise remove or clearly disable the stale deployment entry point.

**Regression check:** Build a clean image and import/start `app.main` without mounting the development environment.

### A22 — Legacy spending limits omit in-flight reservations

**Locations:** [daily usage check](/Users/danielarturi/Developer/NYC_Housing/app/limits/service.py:171), [monthly usage check](/Users/danielarturi/Developer/NYC_Housing/app/limits/service.py:219), [admission and event commit](/Users/danielarturi/Developer/NYC_Housing/app/limits/dependencies.py:100), [committing event helper](/Users/danielarturi/Developer/NYC_Housing/app/limits/service.py:22).

The hosted path checks completed `AnswerLog` usage. It does not reserve the estimated cost or tokens of admitted requests. The PostgreSQL advisory transaction lock in the monthly check is released by the event commit before provider work finishes, so it does not serialize the unrecorded spend interval. Another request can pass against the same remaining budget.

**Impact:** Concurrent answers can exceed daily and monthly limits. The local gateway's reservation ledger does not protect this separate execution path. Real PostgreSQL concurrency was not exercised; the transaction and accounting gap is code-confirmed.

**Recommended correction:** Route hosted paid work through an atomic reservation/settlement mechanism, with failure and uncertain-outcome handling, or explicitly retire this path. Do not fix this by holding a database transaction open throughout model generation.

**Regression check:** Concurrent requests with room for exactly one; assert that only one is admitted. Run the check against PostgreSQL, since SQLite tests do not exercise advisory-lock behavior.

## Architectural improvements

### A23 — Every retained generation duplicates the full-text index without reclamation

**Locations:** [full generation FTS population](/Users/danielarturi/Developer/NYC_Housing/app/corpus/service.py:1011), [current retention scope](/Users/danielarturi/Developer/NYC_Housing/app/maintenance/retention.py:87).

`_build_fts` inserts a full text row for every chunk in every generation. Source updates, resource changes, and indexing transitions retain prior generations. Maintenance prunes operational metadata and cache but does not reclaim old generation FTS rows or define a corpus-history storage budget. Content-addressed chunks therefore do not prevent the full-text index from growing approximately with corpus size times retained generations.

This is a scaling/design limitation, not a measured production outage. Retaining history can be correct, particularly for saved evidence and rollback. The missing piece is an explicit retention and storage model that preserves those references without indefinitely duplicating every searchable body.

**Recommended direction:** Measure multi-generation storage and retrieval costs, index immutable chunks once where practical, and add reference-aware reclamation or compaction. Protect active/rollback generations and versions referenced by saved research. Expose estimated reclaimable space before deletion.

**Validation:** Benchmark repeated small updates to a realistic corpus, measuring database/FTS size and backup size as well as single-generation search latency.

### A24 — API/CLI orchestration and mutable policy ownership need clearer boundaries

**Locations:** [local application composition](/Users/danielarturi/Developer/NYC_Housing/app/local_app.py:102), [CLI entry point](/Users/danielarturi/Developer/NYC_Housing/app/cli/main.py:1), [worker context ownership](/Users/danielarturi/Developer/NYC_Housing/app/jobs/interactive.py:34), [provider policy admission](/Users/danielarturi/Developer/NYC_Housing/app/providers/gateway.py:431).

The local API factory and CLI are each over 2,000 lines and combine transport parsing, validation, configuration mutation, service construction, job admission, and response shaping. The problem is responsibility ownership, not the line counts themselves. Each route or runner can select a different context lifetime or cost-check strategy, as A03 and A19 demonstrate. Repeated manual payload/error handling also makes it easy for ordinary domain operations to escape as internal errors, as A07 demonstrates.

**Recommended direction:** Extract typed request/response models and domain routers; share application-level commands between CLI and browser; centralize live policy, operation approval, and worker lifecycle. Keep immutable job inputs distinct from current privacy/network/budget policy. Consolidate duplicated behavior gradually around the findings rather than undertaking a wholesale rewrite.

**Validation:** Exercise the same command through both CLI and API and assert matching policy, error classification, accounting, and result semantics. Make service-level lifecycle tests the stable contract beneath either interface.

## Original remediation order and missing coverage

1. **Privacy and recoverability:** A01, A02, A03, A06. Establish authoritative consent and live policy; verify the exact backup artifact and restored admission state.
2. **Spending and installation:** A04, A05, A19. Make admission enforce the intended bound and ensure a clean installation supplies all mandatory runtime data.
3. **Persistent state lifecycle:** A07–A09, A17–A18. Cover deletion, crash ownership, unsettled calls, durable job inputs, and retention together.
4. **Evidence correctness and normal interaction:** A10–A16, A20. Prevent wrong-property observations, missing source text, misleading completeness, import conflicts, and blocked UI interactions.
5. **Support boundaries and scale:** A21–A24. Decide the hosted path's status, add native backend coverage if retained, and measure long-lived workspaces.

At the original audited checkout, the green suite did not cover the interactions that produced these failures. The most valuable additions are lifecycle/state-transition tests, cross-feature privacy checks, verification of bytes extracted from final backup archives, two-tab browser tests, mixed/empty property-result tests, overlapping-bundle tests, and clean dependency installations. At that checkout, CI used SQLite for the native Python suite without PostgreSQL concurrency or Docker startup checks. The hardening pass adds both checks.

The findings above preserve the original failure mechanisms and recommendations. Use the hardening record at the beginning of this document for current resolution status, tests, upgrade steps, and remaining limitations. Original location line numbers refer to the audited checkout; the hardening links identify the current implementation and regression tests.
