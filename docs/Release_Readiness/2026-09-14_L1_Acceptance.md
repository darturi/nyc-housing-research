# L1 acceptance evidence — 2026-09-14

Status: **implementation release candidate; external release gates remain open**.

Comparator revision at start: `7eede2ea` (dirty working tree was preserved).
All commands below used isolated local/temp data roots and fake/mock providers
unless explicitly labeled. No commit, push, public repository, corpus publication,
hosted deletion, or credential rotation was performed.

The checkout still contains an ignored legacy `.env` (not inspected) and an
ignored 62 MiB downloaded HMC artifact under `.artifacts/`. Neither is tracked or
present in the wheel/sdist, and neither may be force-added to a public release.

## Evidence obtained

| Gate | Result | Evidence |
| --- | --- | --- |
| Test isolation | Pass | Poisoned parent remote URL and per-test workspace guards; full suite green |
| Lint/unit/service/security/privacy/fault tests | Pass | `uv run ruff check .`; `uv run pytest -q`: 306 passed, 1 opt-in browser test skipped, 4 dependency deprecation warnings |
| Locked package build | Pass | `uv lock --check`; wheel and sdist built from 0.1.0 candidate |
| Wheel and sdist outside checkout | Pass | Fresh Python 3.12 venvs under `/private/tmp`; exact final wheel and sdist setup and schema preflight passed in spaced Unicode paths; wheel status/prune/loopback checks also passed; an earlier candidate wheel imported, verified, and searched a 741-chunk canonical corpus |
| Packaged assets | Pass | Final wheel/sdist build includes templates, static assets, source manifests, HPD connector-v2 manifest, 55 retrieval cases, and 28 legal-review cases |
| Release-content audit | Pass for built archives | Checked-in verifier and native CI step reject traversal/links, runtime state, credentials, logs, private paths, and secret-shaped non-test content; final wheel/sdist pass, while deliberately fake secrets remain allowed only under test fixtures |
| Loopback installed app | Pass | Final clean wheel bound `127.0.0.1`, served HTTP 200 with CSP/no-store/no-referrer/nosniff/frame-deny headers and the final credential-slot/resume/settings UI asset |
| Real browser journey | Pass locally | Opt-in Playwright test passed against a loopback server in headless Chrome: launch-token exchange, first-run source install, OpenAI profile/budget selection, write-only owner-file credential entry, cited answer/evidence/source link, failed-job resume, HPD ambiguity/paging/refresh/source link, and conservative Auto routing; no provider call was made |
| Idle server RSS | Pass locally | 96,128 KiB RSS on macOS arm64, below 500 MB target |
| 10k × 1,536 retrieval | Pass locally | Final run: 92,651,520-byte corpus; startup 0.0023 s; keyword p95 0.0946 s; hybrid cold 0.5098 s; warm p95 0.1022 s; peak process RSS 268,091,392 bytes |
| Live five-source installation | Pass locally | An isolated 0.1.0 candidate wheel's `setup --install-core` downloaded and activated all five modules in 22.9 s; 741 chunks/741 FTS rows; required anchors and citation invariants verified; final changes do not touch acquisition/parsing; earlier measured total was 70.3 MiB |
| Unchanged live update | Pass locally | Repeated publisher downloads with different raw bytes reused generation `112b9134-3bf4-4cf1-a32f-4b94f2c0c8aa`; transient unreferenced artifacts removed |
| Conditional and failed source refresh | Pass in fixtures | ETag/Last-Modified request and valid 304 artifact reuse advanced only `last_checked_at`; missing reusable bytes fail closed; injected atomic publication failure preserved the active generation and prior artifact |
| Deterministic retrieval evaluation | Pass locally | 55 current-source cases; Recall@5 0.9636; MRR 0.8712; two broad MDL paraphrase misses retained in the report |
| Live HPD contract and bounded query | Pass locally | `doctor --online` verified dataset `wvxf-dwi5`; building 375411 returned distinct records; two-page/10-row export stopped and labeled `max_pages_reached` |
| Secret-free backup/restore | Pass | Corrupt, active-job, sanitization, ledger/corpus round-trip tests |
| Read-only legacy migration fixture | Pass | Exact evidence/provenance bundle round-trip; vectors excluded as incompatible |
| HPD connector/cache/export fixtures | Pass | Typed IDs/address/ZIP/class/status/date requests, ambiguity, nullable totals, top-level paging, fetch interval/source timestamp, stale cache, formula-neutral CSV, bounded complete export |
| No-key usefulness | Pass in fixtures | Exact/FTS search works; provider/embedding failures preserve evidence |
| Spend accounting | Pass | Reservation/settlement/uncertainty/correction, rollover, concurrency, offline-before-reserve, and truthfully separate unknown-cost attempt tests |
| Advanced provider isolation | Pass in fixtures | Separate credential slots, endpoint-derived profile IDs, HTTPS/loopback validation, complete-or-unknown price/privacy metadata, blocked-before-check behavior, explicit unknown-cost one-off admission, automatic batch denial, and no-fallback Responses/embeddings contract checks |
| Redacted diagnostic export | Pass | Owner-only atomic report omits questions, addresses, prompts, response bodies, credentials/tokens, and private paths; no network or paid call is made |
| Retention safety | Pass | Independent policies; preview/apply; active-job/provider-call barrier; pinned cache, unresolved spend, and current-month protections; installed-wheel smoke |
| Answer-evaluation tooling | Pass technically | Free estimate and 26-case synthetic dry run completed with one operation ID and zero cost; report includes reviewer inputs and remains `domain_review_required` |

Final local artifact hashes are recorded after the last documentation-bearing
rebuild and clean installed-wheel smoke:

- Wheel SHA-256: `a7d5ed8b52f969e26f72906fcd88e0918df158a8e0af04c9136b28dd7f8786fa`
- Source distribution SHA-256: `78651b40ab250db8e177f978d1b299db652493b5969b49740368723cf1aae6ef`

The synthetic benchmark is intentionally not a legal-quality result. Peak RSS
includes fixture construction plus querying; idle RSS was measured separately on
the clean installed serve process.

## Work-package disposition

| Package | Candidate disposition | Remaining release evidence |
| --- | --- | --- |
| A01–A03 baseline/config/package | Implemented and locally verified | Native Linux/Windows CI and a verified GitHub remote |
| B01–B05 corpus/migration/retrieval | Implemented; official install/update and deterministic retrieval pass | Controlled PostgreSQL comparator for the required parity delta |
| C01–C05 security/providers/answers/UI | Implemented with synthetic/offline, fault, and local real-browser coverage, including provenance-complete answer evidence and the bounded answer-evaluation runner | Explicitly budgeted real-provider evaluation and domain-reviewed answer results |
| D01–D03 HPD/cache/property integration | Connector v2 implemented; bounded public API and complete-export contract live-verified; local real-browser workflow passed | Independent browser journey on each advertised platform |
| E01 recovery/operations | Implemented and locally verified, including preview-first configurable retention | External clean-machine recovery journey remains |
| E02 release proof | Local macOS, clean wheel, browser, live-source, HPD, retrieval, and performance evidence complete | Cross-platform CI, independent operator, paid evaluation, parity, and legal review |
| E03 release handoff | Documentation, release notes, compatibility matrix, audit, wheel, and sdist prepared | License/attribution decision, repository destination, CI records, and explicit publication approval |

### Live core inventory

The clean text-only install was verified on 2026-09-14 America/New_York
(publisher fetch timestamps were 2026-09-15 UTC):

| Source | Chunks | Download bytes | SHA-256 |
| --- | ---: | ---: | --- |
| NYC Housing Maintenance Code | 153 | 65,491,533 | `c26cd24efd7a40b1b71416fde399622e5d1839bfaabfd4694f0c2971b544efcb` |
| New York Multiple Dwelling Law | 193 | 400,769 | `ff497b229b4a9f3276627d8f676c8a29caabad323c73e3dae627c30ae2d29793` |
| RPAPL | 365 | 416,616 | `85d5249ec4a9ba974b4239f40d361735a5b2d546d6690de90b795bda767263a7` |
| Real Property Law Good Cause provisions | 8 | 813,814 | `d1cb1bd09b73b017c36c664058cdb098649c6fb7bb167fdf6bfcb321caa9b79e` |
| HPD tenant/owner guidance | 22 | 150,623 | `d16a87e66fbdfd30a8d4ffd1e7923b15586e9a1795daa4b1c7410379858c02a0` |

The five content-addressed downloads occupied 64.2 MiB, `corpus.sqlite3`
occupied 6.0 MiB, and the complete fresh workspace occupied 70.3 MiB. These
hashes are observations, not permanent publisher identifiers; future verified
source changes should produce new versions.

## Open external/release evidence

| Gate | Status | Needed action |
| --- | --- | --- |
| GitHub repository and CI | Blocked by missing remote | Establish verified remote; run Linux/macOS/Windows workflow before advertising those platforms |
| Code license | Maintainer decision required | Review the prepared dependency/source inventory, select/add `LICENSE`, and finalize notices |
| Frozen PostgreSQL parity | Environment unavailable | Run the same 55 cases/vectors through the controlled read-only comparator; require recall@5/MRR delta no worse than 0.02 |
| Real OpenAI answer evaluation | Key/budget approval absent | Explicitly approve bounded evaluation; record profile/prices/cost and retain no transcript by default |
| Legal-domain review | Reviewer unavailable | Review designated must-pass claims/qualifications; retrieval marker validity is not substantive legal correctness |
| Independent clean-machine journey | Second operator unavailable | Complete documented setup/search/key/answer/property/update/export/restart/recovery journey |
| Source redistribution | No affirmative decisions | Keep source artifacts/bundles out of publication; default installer downloads official sources |

## Known limitations and dispositions

- L1 deliberately uses live/cached filtered HPD lookup rather than the assessed
  roughly 23 GB bulk database. Full offline/citywide analytics remains L2.
- Legacy S3 artifacts must be privately staged locally for read-only export.
- Property cache and exports retain the acquisition start/completion interval and
  an optional publisher response timestamp. Multi-page exports still disclose that
  pages are not a transactional snapshot.
- The UI covers core research/settings/property flows with API integration tests
  and a checked-in real-Chromium fixture journey. A cross-platform browser run and
  independent operator journey remain part of the GitHub release gate.
- Core corpus install/update, durable status/cancel/resume, verification, and
  rollback are available in the browser. Semantic-index estimation/explicit paid
  approval, deliberate partial-module activation, loaded-page export, and tracked,
  cancellable, bounded multi-page property export are also available there.
- Interactive setup is network-free unless the operator accepts the explained
  five-source text-only installation offer. Explicit `--install-core` and
  `--skip-core` flags make noninteractive behavior deterministic; credential setup
  remains an explicit hidden-input option. An absent corpus opens the browser's
  first-run Sources guidance.
- Retention cleanup is preview-first and independently configurable for
  operational records/logs, property cache, and completed usage. Applied cleanup
  refuses active jobs/provider calls and preserves nonterminal jobs, pinned cache,
  unresolved spend, and the current budget month; pruned history is disclosed.
- The update checker requires an explicit verified GitHub URL because this checkout
  has no remote; it takes no action beyond reading release metadata.
- Advanced OpenAI-compatible overrides retain the selected packaged model/request
  contract rather than accepting arbitrary model schemas. Their price source,
  prices, and response-retention flag are operator assertions; endpoint changes
  get a new effective profile ID and every override change is blocked until an
  explicit no-fallback check. An explicitly unknown price blocks all automatic
  and batch operations; separately approved one-off requests are labeled outside
  USD caps and counted without claiming a dollar amount.
- The packaged synthetic answer profile is not a quality model. Its 26-case dry run
  intentionally did not satisfy 12 technical citation/source/status checks; this is
  evidence that the evaluator fails visibly, not answer-quality acceptance.

See the [2026-09-15 requirement audit](2026-09-15_L1_Requirement_Audit.md) for
feature- and work-package-level traceability.

## Release decision

Do not label 0.1.0 L1-complete or publish it as open source until every open gate
above is either passed with evidence or explicitly re-scoped by the maintainer.
The implemented candidate is usable locally and removes the architectural need
for maintainer hosting, but release logistics and substantive legal validation
cannot be manufactured by code inspection.
