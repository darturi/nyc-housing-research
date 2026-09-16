# NYC Housing RAG: Local Distribution Feature Specification

Status: proposed target-state specification, not a description of current implementation.

Date: 2026-09-14. Target: first supported local release, referred to as **L1**.

Execution companion: [Local Distribution Implementation Plan](Local_Distribution_Implementation_Plan.md).

## 1. Product objective

Let someone clone the GitHub repository, supply their own model API key, and
run a useful NYC housing research application on their computer. The project
maintainer should not need to operate an application server, a shared database,
an account service, a model proxy, or object storage for users.

Preserve the existing legal corpus, citation lookup, hybrid search, legal
answers, and address-specific HPD research. Make wider legal coverage and bulk
property research possible through optional modules without making them
prerequisites for installation.

The product is locally operated but normally internet-connected: model requests
go directly to the user's selected provider, and public-data requests go
directly to the source agency. GitHub distributes code and release metadata;
permitted static data packages may also be release assets. This distinction
must be visible in documentation and setup.

This specification governs the distribution overhaul. The existing
[MVP implementation plan](MVP_Implementation_Plan.md) remains a record of the
original hosted design. The broader
[project plan](NYC_Housing_Law_RAG_Project_Plan.md) remains a source of future
corpus and research ideas; its infrastructure list is not a dependency list
for the local edition.

### 1.1 Product decisions

| Decision | Specification |
| --- | --- |
| Default installation | One local Python application and browser UI |
| Default database | SQLite, with real full-text search and local vector retrieval |
| Default artifacts | Files in a user-owned data directory |
| Model usage | User-supplied credentials, direct provider requests |
| Default HPD lookup | Filtered NYC Open Data requests with a local cache |
| Full HPD dataset | Optional extension, excluded from initial setup |
| Default access | Single local user, loopback-only service |
| Required maintainer infrastructure | GitHub repository and releases; no running service |
| Legal source acquisition | Download official sources on the user's machine |
| Prebuilt corpus distribution | Optional, only for source content approved for redistribution |
| Updates | User-controlled code and corpus updates, with resumable jobs |
| Query history | Off by default; aggregate usage accounting remains local |
| Quality promise | Same supported legal coverage and comparable retrieval quality, measured before release |

### 1.2 Baseline evidence and limits

The September 14 repository assessment found:

- 690 current legal/guidance chunks: HMC 153, MDL 179, RPAPL 329,
  Good Cause Eviction 7, and HPD guidance 22.
- Approximately 1,636,260 characters of current chunk text, or about 409,000
  tokens using a rough four-characters-per-token estimate.
- 1,198 total stored chunks and embeddings when superseded source versions
  are included. This total is not the active retrieval corpus size.
- Approximately 11.1 million HPD rows according to PostgreSQL statistics,
  occupying approximately 23 GB including indexes.
- Existing fake-provider/SQLite tests pass, but the current SQLite keyword
  fallback is not equivalent to PostgreSQL full-text search.
- The configured database was at migration `20260713_0009`, although that
  migration and other changes remained uncommitted.

These are measured starting conditions, not fixed limits or release promises.
Changing storage must not silently remove current sources. The current 690
chunks are also not a claim of complete NYC housing-law coverage.

### 1.3 Operational responsibilities

| Owner | Responsibilities |
| --- | --- |
| Maintainer/contributors | Package releases, repair source adapters, review coverage and quality, maintain setup documentation and supported-platform tests |
| User | Install the app, supply and pay for provider access, approve updates, manage local disk/backups, and protect credentials |
| External publishers/providers | Serve official data/model APIs under their own availability, access, and rate-limit policies |

No maintainer-operated service is required, but ongoing software/source
maintenance remains necessary. Release notes and coverage manifests communicate
known breakages; users retain the last usable corpus when an external source
changes or becomes unavailable.

## 2. Users, journeys, and release scope

### 2.1 Intended users

- **Individual researcher:** asks legal questions, checks original sections,
  and looks up a property without managing a database server.
- **Tenant/owner advocate or practitioner:** checks sources and dates, needs
  qualified answers, and can export a research result for later review.
- **Advanced researcher:** installs more legal sources or a full property
  snapshot and runs repeatable research on that local data.
- **Contributor:** can install, test, reproduce a bug, and extend a source
  adapter without production credentials or a shared development database.

L1 assumes one person operating the app on their computer. Public access,
collaborative accounts, shared office deployment, and mobile clients are
outside L1's supported environment.

### 2.2 Principal journeys

1. **First useful result:** clone a tagged release, install dependencies,
   initialize a local workspace, configure a key, build the legal corpus,
   open the UI, and ask a cited question.
2. **Source-only research:** initialize without a key, ingest available legal
   sources, and use exact-citation and keyword search without paid requests.
3. **Property lookup:** enter an address, confirm the building if ambiguous,
   inspect paginated HPD records and their freshness, and optionally request
   a bounded model summary.
4. **Update:** inspect source changes and estimated cost, build changed
   content in the background, then activate a validated corpus generation.
5. **Reproduce:** export a result with sources, corpus version, and model
   profile, or produce a redacted diagnostic report for a GitHub issue.
6. **Advanced/offline use:** explicitly install a bulk-data extension, choose
   that snapshot in the UI, and receive clear labels about its coverage/date.

### 2.3 Feature priority matrix

L1 features are required for the first supported local release. L2 features
are specified extension work that can ship separately. Deferred features
remain outside the acceptance criteria for both unless separately scoped.

| ID | Feature | Priority |
| --- | --- | --- |
| F01 | Reproducible native installation and local startup | L1 |
| F02 | Setup, credentials, and model profiles | L1 |
| F03 | Local storage and workspace isolation | L1 |
| F04 | Legal-source ingestion, provenance, and version activation | L1 |
| F05 | Local exact, keyword, and vector retrieval | L1 |
| F06 | Grounded answers and evidence inspection | L1 |
| F07 | Live HPD lookup, disambiguation, and caching | L1 |
| F08 | Optional full HPD snapshot and structured analytics | L2 |
| F09 | Research, sources, settings, and maintenance UI | L1 |
| F10 | Shared usage ledger, budgets, and cancellation | L1 |
| F11 | Local access security and truthful privacy controls | L1 |
| F12 | Update jobs, diagnostics, and reproducible exports | L1 |
| F13 | Migration from the current PostgreSQL/S3 setup | L1 |
| F14 | Documentation, CI, and release packaging | L1 |
| F15 | Additional official legal-source modules | L2 |
| F16 | Optional container distribution | L2 |
| F17 | Saved research history and fully local model profile | L2 |

Deferred: case-law ingestion, citators, knowledge graphs, BBL/parcel joins,
geospatial analysis, unrestricted natural-language-to-SQL, arbitrary user
document uploads, public hosting, multiuser administration, and a signed
native desktop installer. Case law and BBL can be added as explicit source
modules later; the distribution change does not prohibit them.

## 3. Capabilities and network modes

The interface must distinguish installed capability from current availability.

| Capability | Requires internet? | Requires model key? | Behavior when unavailable |
| --- | --- | --- | --- |
| Read installed legal text and citations | No | No | Explain missing source if not installed |
| Keyword and exact-citation search | No | No | Available once text is installed |
| Semantic search with a remote embedding model | Yes, for a new query embedding | Yes | Use labeled exact/keyword retrieval |
| Generate an answer with a remote model | Yes | Yes | Show relevant source excerpts and provider status |
| Live HPD lookup | Yes | No model key; source token may be needed | Offer matching cached results with age and coverage |
| Cached HPD lookup | No | No | Explain cache miss rather than claim zero violations |
| Download/update legal text | Yes | No | Keep the active corpus usable |
| Embed new/changed text remotely | Yes | Yes | Pause the job; retain completed work |
| Query an installed bulk HPD snapshot | No | No | L2 extension; display snapshot date |

An explicit offline switch disables all outbound application HTTP calls,
including update checks. A locally configured model server may be allowed as
a separate user-selected offline profile. L1 offline operation guarantees
source reading, exact/keyword search, and cached-data inspection; it does not
promise new remote-model answers or new remote query embeddings.

## 4. Target architecture

```text
Local browser
  -> FastAPI on a loopback address
       -> Research services
            -> Legal retrieval -> local corpus + full-text/vector indexes
            -> Property lookup -> official public API + local response cache
            -> Answer synthesis -> user's model provider
       -> Local job runner -> downloads, parsing, embeddings, activation
       -> Application state -> settings, jobs, budgets, local access session
       -> Optional extensions -> bulk property snapshot / additional sources
```

### 4.1 Runtime boundaries

- Keep the existing Python/FastAPI application and lightweight browser UI.
- Default to one web worker. A durable local job runner handles long work;
  neither Redis nor a separate managed queue is required.
- CLI and web operations call the same services for source validation, cost
  accounting, downloads, query execution, and job cancellation.
- Use a property repository interface so live API, cache, and optional bulk
  lookup return the same record/provenance contract.
- Use a retrieval interface so changing the vector index later does not
  change citations, corpus manifests, or public API contracts.
- Move PostgreSQL, S3, and later bulk-engine dependencies to optional extras.
  Legacy support may remain internally during migration; it is not required
  for a clean local installation.

### 4.2 Data ownership

Separate the rebuildable legal corpus from mutable application state:

- `corpus.sqlite3`: source versions, documents, sections, chunks, citations,
  embeddings, full-text index, and staged/active generation membership.
- `state.sqlite3`: settings excluding secrets, jobs, usage ledger, property
  cache metadata, and local session state.
- `artifacts/`: original source downloads and retained property responses.
- `exports/`: user-requested results, diagnostics, and migration bundles.
- `backups/`: consistent local backups made before schema changes.
- `extensions/`: optional data stores with their own manifests.

Queries acquire a fixed active generation at request start. Activation changes
one generation pointer transactionally inside the corpus database after all
required validation succeeds. The usage ledger is independent and survives
corpus rebuilds, switches, and rollbacks.

## 5. Detailed feature requirements

### F01. Installation and startup

#### User experience

The canonical workflow, after installing Git and uv, is:

```sh
git clone <published-repository-url>
cd NYC_Housing
uv sync --locked
uv run nyc-housing setup
uv run nyc-housing serve
```

`nyc-housing` is a proposed executable entry point. These commands must be
implemented before they appear as working quickstart instructions. The
published repository URL is a release-time value, not an invented URL.

`setup` creates local configuration and storage, offers credential setup,
and starts a resumable core-corpus installation. It explains download work
and estimated paid embedding work before starting that work. The user may
choose source-only installation with no paid requests.

`serve` starts immediately, opens the default browser if possible, and prints
a usable loopback URL. Browser-open failure does not stop the service. An
unfinished installation opens the Sources/Setup view. Port conflicts produce
a clear message and support `--port`; `--no-browser` supports terminals and
remote-shell development.

#### Requirements

- Pin a tested Python minor version in `.python-version`; start with the
  existing Python 3.12 baseline unless packaging validation requires a change.
- Lock runtime and development dependencies in `uv.lock`.
- Use a proper package build configuration and console entry point. Include
  templates, static assets, migrations, manifests, and evaluation fixtures
  needed by installed commands in the package.
- No database server, cloud account, manual SQL, manual admin-user creation,
  compiler, or GPU is required by the native core install.
- A bundled, explicitly synthetic demo works without keys or downloads and
  is labeled as a demonstration, never as legal reference content.
- Running `serve` never silently initiates embedding or bulk ingestion.
- Store runtime files outside the Git checkout by default; support
  `--data-dir` for portable installations and tests.

#### Acceptance

- A clean macOS, Windows, and Linux test environment can execute the documented
  workflow using only its documented prerequisites.
- The same commands work with a home/data path containing spaces and Unicode.
- Repeated setup resumes or reports completion without duplicating data or
  overwriting settings.
- A keyless source-only installation starts, searches installed text, and
  explains how to enable model-backed answers.
- Restarting the application preserves corpus, settings, budgets, and cache.

### F02. Credentials and model profiles

#### Requirements

- Support one user-supplied key for the default provider's embedding and
  answer requests. Advanced settings may supply separate keys/endpoints.
- Native setup accepts hidden terminal input and stores it in the operating
  system credential store when available. Support environment variables for
  users who prefer managing secrets themselves.
- If no credential store is available, offer environment-based setup and
  instructions for a user-managed secret file. Never silently persist a key
  as ordinary application settings or browser storage.
- Resolve credentials server-side. Settings APIs return only presence and
  provider status; they never return an existing secret.
- Credential replacement/validation from a local UI is a write-only operation
  protected by the local access and request-origin controls in F11.
- Default profiles specify provider, model IDs, dimensions, tokenizer/input
  handling, context/output limits, timeout policy, and price-reference date.
- Treat the current `text-embedding-3-small` / pinned answer-model configuration
  as a migration baseline, not an assertion that it is the best supported
  profile at release. Select and test the released profile before publishing.
- An OpenAI-compatible endpoint is an advanced configurable option with an
  explicit compatibility check. Changing providers never triggers silent
  fallback to another provider or model.
- Switching only the answer model does not require re-embedding. Changing the
  embedding model, dimensions, or preprocessing creates a distinct embedding
  profile and requires a compatible index before semantic search is enabled.
- Missing, invalid, or exhausted keys do not prevent the app from starting.
- Key validation reports the operation and whether it may incur a charge;
  any paid validation participates in the usage ledger.

#### Acceptance

- Fake, missing, invalid, and working credentials each produce actionable UI
  states without exposing their values in logs or diagnostics.
- A dimension/profile mismatch is caught before a vector comparison.
- A changed answer model leaves existing embeddings intact.
- A partially re-embedded corpus is never presented as a complete semantic
  index for the new profile.

### F03. Local persistence and isolation

#### Requirements

- Use OS-appropriate user config/data directories and expose their locations
  through `status`, `doctor`, and Settings. An explicit data directory wins.
- Enable SQLite foreign-key enforcement on every connection. Use bounded
  busy timeouts and WAL mode where supported on a local filesystem.
- Support one coordinated writer per workspace for corpus updates; readers
  continue using the active generation. Conflicting CLI/web maintenance jobs
  queue or return a clear busy state rather than corrupt data.
- Keep vector data in an explicit numeric format with a declared dtype,
  shape, checksum, and profile. Do not deserialize executable objects such as
  pickle from corpus/data packages.
- Compute active and retained counts separately in status reports. Display
  text-ready and embedding-ready counts for the active profile.
- Keep data-directory changes and workspace resets explicit. A user can
  export or back up a workspace before deleting it.
- Apply schema migrations through a supported command/startup preflight with
  a backup before changing an existing schema. Refuse incompatible newer
  schemas without attempting a destructive downgrade.
- Use SQLite's consistent backup mechanism or a stopped/checkpointed database;
  copying only a live `.sqlite3` file while omitting WAL data is not a backup.

#### Acceptance

- A crashed write/update leaves the previous active corpus queryable.
- A schema-upgrade failure provides a documented restore path.
- Corpus reset does not erase credentials, usage accounting, or user exports.
- `doctor` identifies unsupported network/cloud-synced database locations and
  provides a supported local-directory alternative.

### F04. Legal corpus, ingestion, and provenance

#### Core source scope

| Module | Required coverage |
| --- | --- |
| `nyc-housing-maintenance-code` | HMC sections from the configured official code publication |
| `ny-multiple-dwelling-law` | Configured official Multiple Dwelling Law source |
| `ny-rpapl` | Configured official RPAPL source |
| `ny-real-property-law-good-cause` | RPL Article 6-A, sections 210–216; not all Real Property Law |
| `hpd-guidance` | Curated official pages including complaints, follow-up, enforcement, and clearing/certifying violations |

HPD violations are a property-data connector, displayed separately from legal
text coverage. Guidance pages can be cited by title and official page URL;
the absence of a statute-style section number is not itself a citation defect.

#### Acquisition

- Default installation fetches official artifacts directly using checked-in
  source manifests and parsers. Every manifest records publisher, jurisdiction,
  source URLs, expected formats, coverage, adapter version, and use/redistribution
  review status.
- Support the current AmLegal bulk XML, official NY Senate statute artifacts,
  and curated HPD page bundle, subject to live access verification at release.
- Respect source access constraints. Provide a manual official-artifact import
  with provenance fields when automated access fails.
- One failed source does not erase another source's successful installation.
  The app reports `partial` coverage and precisely identifies the missing module.
- Zero parsed sections, unexpected sharp count changes, missing required section
  anchors, or structural parser errors prevent automatic activation for that
  source and require review through the job result.
- Merely downloading a file must not mark its version current. Download,
  parse, validate, index, and activation are separate stages.

#### Versions and updates

- Distinguish `retrieved_at`, `last_checked_at`, known legal effective dates,
  parser version, content hash, and generation ID. A retrieval date is not a
  legal effective date.
- Use conditional requests where supported; an unchanged content hash updates
  the last successful check without new chunks or paid embeddings.
- Hash normalized chunk content and preprocessing/profile identity so unchanged
  chunks can reuse embeddings across source versions.
- Build changes in a staged generation. Validate required source anchors,
  citations, artifact hashes, and complete profile coverage before normal
  activation. Preserve the prior generation on error or budget exhaustion.
- A user may activate a clearly labeled text-only/partial generation; this
  requires a deliberate selection rather than silently weakening readiness.
- Freeze a generation for reproducible research and allow rollback to the
  previous retained generation. Preserve the active and immediately previous
  generation by default; other retention is size/age controlled.
- Artifact pruning follows references from retained generations and manifests,
  including all referenced HPD page objects. Never delete referenced data just
  because a source-version timestamp expired.

#### Optional distribution packages

- A code release includes source recipes and format schemas. Prebuilt content
  is optional; installation must work without maintainer-built corpus assets.
- A content package contains a manifest, canonical records, source provenance,
  checksums, coverage, and parser versions. Embeddings, if distributed and
  permitted, include complete profile identity and licensing metadata.
- Import validates format version, references, file paths, decompressed sizes,
  and hashes before activating anything. Data packages never execute SQL,
  Python, or shell scripts supplied by the package.
- Public packages contain no sessions, keys, questions, usage logs, private
  exports, or local absolute paths.
- Package inclusion requires a recorded source-specific redistribution decision.
  A source being accessible on the web is not sufficient evidence by itself.

#### Acceptance

- Core coverage equals the migration baseline for an identical artifact set;
  additions/removals from newer official versions are reported explicitly.
- An update with unchanged content causes zero new embedding requests.
- An interrupted embedding job resumes completed batches without charging again
  for locally committed results.
- A failed parse cannot remove the currently usable source from retrieval.
- Traceability verifies referenced content bytes/checksums, not just whether a
  top-level manifest exists.

### F05. Local retrieval

#### Requirements

- Preserve exact normalized citation lookup, source/jurisdiction filters, and
  exclusion of inactive, ineligible, or superseded sources.
- Implement SQLite FTS5 with weighted title/citation/body matching. SQLite
  provides a BM25 rank function; convert or rank-fuse its lower-is-better
  output deliberately rather than directly mixing it with cosine scores.
  See the [official FTS5 documentation](https://www.sqlite.org/fts5.html).
- Check FTS5 availability at install time on each supported runtime. L1 must
  not silently ship the current substring-scanning test fallback as production
  full-text search.
- Use normalized float32 vectors and exact cosine ranking for the initial
  corpus; the numeric implementation must have a tested wheel on supported
  platforms. Keep index access behind an interface for future scale changes.
- Generate at most one query embedding per distinct question/profile/request,
  even when retrieval explores multiple source filters.
- Combine citation, keyword, and vector ranks deterministically, with measured
  preference for an exact requested provision. Domain expansions remain
  explainable and covered by evaluation cases.
- Respect source filters before returning/reranking candidates. Retrieve only
  from one captured active generation per request.
- On embedding-provider failure, complete exact/keyword search and label the
  missing semantic component. Do not turn an available exact citation into
  an HTTP 500 because an unrelated embedding call failed.
- Never mix vectors from different profiles or silently interpret an unembedded
  chunk as absent from the legal corpus.
- Expose retrieval-only mode in the UI, requiring no answer-generation charge.

#### Acceptance

- Exact-citation, filtered, paraphrased, and negative queries pass the frozen
  retrieval evaluation described in Section 9.
- Retrieval-only offline search emits zero outbound requests.
- For identical source/profile data, SQLite satisfies the measured quality
  parity thresholds relative to the existing PostgreSQL implementation.
- Results and diagnostics identify the active generation and retrieval methods.

### F06. Answer quality and evidence

#### Requirements

- Preserve general housing information, qualifications, unsupported-answer
  behavior, and a concise legal-information disclaimer.
- Supply only the question, selected retrieved excerpts, required evidence
  metadata, and system instructions to the answer provider.
- Separate source content and user instructions in the prompt. Treat downloaded
  source text as evidence, never as executable instructions.
- Validate cited evidence IDs against the exact excerpts supplied to the model.
  A source ID being valid does not prove that it supports a proposition;
  statement support remains part of the quality evaluation.
- Show human-readable inline citation markers linked to an evidence panel.
  Each source exposes title/citation, exact local excerpt, original URL,
  publisher, retrieval/check dates, and known effective-date metadata.
- Keep internal UUIDs out of ordinary prose. Preserve machine-readable IDs in
  the API and exports where needed for reproduction.
- Long sections must be handled with subsection-aware context selection or
  explicit excerpt limits. Do not average arbitrarily large legal sections
  into one vector and then silently omit material qualifications from the
  answer context without measuring the effect.
- State unavailable coverage when the question asks beyond installed sources.
  As-of/historical questions are unsupported unless the selected evidence
  supports the requested legal period; cached old downloads alone do not
  establish historical validity.
- Do not infer individualized outcomes, rent-stabilization status, or Good
  Cause applicability from incomplete property facts. Show the missing facts
  and the general source-backed framework when supported.
- A provider outage retains retrieved evidence and gives a retry/source-only
  choice. Partial/truncated provider output is labeled or rejected, never
  shown as a verified complete answer.
- A combined legal/property query uses separate labeled evidence groups and
  a confirmed property identity before synthesis. It may offer a follow-up
  property lookup if automatic identification is uncertain.

#### Acceptance

- No unprovided evidence IDs become user-visible citations.
- Required core review cases pass for support, material qualifications, and
  refusal behavior under the released profile.
- Inspecting an answer's sources requires no further model request.
- Model failure or a denied budget does not suppress usable retrieved excerpts.

### F07. Live HPD lookup and cache

#### Query and identity handling

- Accept building ID, registration ID, or structured house number/street/
  borough/ZIP fields. The natural-language entry point maps into that same
  typed request and shows the resolved property.
- Preserve meaningful house-number punctuation, including hyphenated Queens
  addresses and suffixes. Use a tested street-alias normalization table;
  avoid arbitrary substring matching as the primary identity rule.
- If an address yields multiple buildings/boroughs, return candidates and
  request a selection in the UI before attributing violations to a property.
- Support violation class, source-defined status, and date filters. Preserve
  original status values and document any simplified open/closed mapping.
- BBL lookup stays unsupported until a reliable mapping source is added.
- Build public API requests from allowlisted fields and operators with proper
  literal escaping. Never execute arbitrary user/model-provided SoQL or SQL.

#### Source/API behavior

- Query the [official HPD dataset](https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5)
  with property filters and an explicit field projection. Avoid a citywide
  download for an address lookup.
- Store dataset ID, API dialect/version, field mapping, status mapping, and
  connector version in a manifest. Detect missing/changed required fields
  and report a connector compatibility error.
- Support a user-supplied Socrata app token. During implementation, verify
  anonymous access against the selected public endpoint; never promise that
  every API version is anonymous. Socrata documents continued SODA2.1 support
  and identification requirements for most SODA3 requests. See its
  [SODA3 announcement](https://support.socrata.com/hc/en-us/articles/34730618169623-Introducing-the-new-SODA3-API)
  and [application-token documentation](https://dev.socrata.com/docs/app-tokens.html).
- Prefer the existing public SODA2.1 adapter for L1 if its contract test passes;
  isolate it so a SODA3 adapter can be selected without changing UI semantics.
  If identification is required, guide token configuration while keeping
  legal research and existing caches usable.
- Apply a shared outbound concurrency limit, bounded retries, `Retry-After`
  handling, and an overall request deadline. An outage/rate limit must never
  produce a successful empty-result response.
- Fetch 50 rows per page by default, at most 100 per interactive page. Use
  deterministic ordering with a unique tie-breaker and a connector-managed
  continuation cursor. Account for API-specific pagination semantics.
- Return `has_more`, loaded-row count, and an optional total only if separately
  obtained for the identical filter. Do not synchronously require an expensive
  exact total to show the first page.
- Explain that pages fetched at different times from a mutable source may not
  form a transactionally consistent snapshot. Deduplicate by violation ID.

#### Cache contract

- Cache key includes resolved property identity, all filters, connector/schema
  version, and pagination identity. Negative results are cached only after a
  successful, complete empty response for that exact request.
- Default freshness: 24 hours for successful property data; 1 hour for a
  verified empty result. Cache freshness is local policy, not an assertion
  of how frequently the agency updates its dataset.
- Retain stale successful entries up to 30 days within a configurable 500 MB
  default cache cap. Evict least-recently-used unpinned entries first.
- Display `live`, `cached`, `stale cache`, `bulk snapshot`, `partial`, or
  `unavailable`, plus fetched time and source-update time when known.
- A stale cached result may be used during an outage only with a visible age
  label. A cache miss is not evidence that the building has no violations.
- Cached pages are not a complete property history until all requested pages
  were fetched successfully. Maintain completeness separately from freshness.
- Offer Refresh, Next page, Open official source, and Export loaded results.
  Explain whether an export covers loaded pages or a completed full fetch.

#### Optional synthesis in L1

An explicit Summarize action may combine fetched property records with retrieved
legal text. It reports record count, filters, fetch dates, pagination limits,
and evidence references. It cannot infer that an unobserved violation does not
exist, that a violation is currently unresolved without status evidence, or
that a property legally qualifies for a regime based solely on HPD data.

#### Acceptance

- Tests cover exact IDs, abbreviations, punctuation, hyphenated numbers,
  borough ambiguity, incomplete addresses, pagination, duplicates, and status
  filters.
- A normal property lookup performs bounded filtered requests and never scans
  or downloads a full citywide table locally.
- Failed/timeout/rate-limited calls, verified zero matches, and offline cache
  misses have distinct response/UI states.
- All cached/offline results disclose their age and completeness.
- The old `22 FRONT STAGG STREET` example is not a positive assertion until
  verified against the authoritative source; release tests use a verified
  property identity and separately frozen fixture records.

### F08. Optional bulk HPD extension (L2)

#### Purpose and scope

Preserve an upgrade path for people who need citywide research, reproducible
aggregate queries, or property lookup without relying on a live API. This
extension does not require any service operated by the maintainer.

#### Requirements

- Enable explicitly through `data install hpd --mode full`; never as a setup
  default or an automatic consequence of a cache miss.
- Before downloading, show estimated compressed/uncompressed size, staging
  space, expected records when available, and progress/resume behavior. Base
  disk checks on the chosen format; do not promise the current 23 GB footprint
  will compress to a specific size without measurement.
- Fetch official bulk exports or permission-compatible paginated data directly.
  Optional release manifests may point to official downloads without mirroring
  the dataset under the maintainer's account.
- Initially prototype DuckDB over local typed data/Parquet and benchmark it
  against an indexed SQLite store before choosing the extension's format.
  The engine choice is an L2 benchmark decision, not an L1 dependency.
- Preserve all records/fields required by the declared module scope; publish
  any intentional projections or omissions. Avoid duplicating full raw JSON
  in every normalized row when the source artifact is already retained.
- Partition/index for supported queries and benchmark first-page address lookup.
  Use optional totals so a count cannot block interactive results.
- Initial structured analytics: filter by borough, class, status, and source
  dates; count/group by those fields and time bucket; rank buildings by an
  explicitly stated violation metric. Export aggregates with methodology.
- No arbitrary model-generated SQL. Query templates validate parameters,
  resource limits, and the selected snapshot.
- Resumable jobs retain actual mode: `auto` resumes an interrupted delta as a
  delta, not as an unrequested new full snapshot.
- Derive an incremental cursor from a verified change-tracking field if the
  source exposes one. `currentstatusdate` is not assumed to capture every edit
  or deletion. If used provisionally, label deltas best-effort and reconcile
  periodically with a full export.
- Reject/quarantine sentinel and implausible future timestamps for watermark
  advancement. Preserve the original record for inspection. Never advance a
  watermark from an invalid value such as `9999-12-11`.
- Keep full source timestamp precision and a unique tie-breaker in cursors.
  Commit a completed watermark only after the run's validation succeeds.
- Full reconciliation must account for records absent from the replacement
  snapshot; upsert-only processing cannot detect source deletions.
- Serve the old complete snapshot while building a replacement, then switch
  atomically. Allow rollback and explicit uninstall of the exact extension.
- Label snapshots with acquisition start/end and consistency limitations.
  Historical records in today's dataset are not a reconstruction of the
  database's state on every historical date.

#### Acceptance

- Offline property lookup and the supported aggregate queries work after
  installation with zero outbound calls.
- Interrupted full/delta runs resume without silent omissions or duplicate IDs.
- Malformed dates cannot poison the next run's lower bound.
- An authoritative replacement snapshot removes/tombstones records that are
  no longer present, according to the connector's declared policy.
- Core installation and operation remain independent of the extension.

### F09. Browser experience

Keep the interface small and organized around three primary destinations:

| View | Required content/actions |
| --- | --- |
| Research | Question input; Answer / Search sources / Property mode; evidence panel; property identity; cancel; export |
| Sources | Installed modules; coverage; active version; last checked/fetched; embedding readiness; update/retry; job progress |
| Settings | Provider/profile; credential status; spend/limits; data paths; cache/privacy/offline controls; diagnostics |

- Route automatically where reliable, but allow the user to select or correct
  the mode. A citation question mentioning a building must not automatically
  become a property request.
- Ambiguous property results present concrete candidates, not a guessed answer.
- Show progress stages for long operations, including source download, parsing,
  embeddings, validation, and waiting on the provider.
- Separate missing sources, no matches, unsupported question, unavailable
  provider, exhausted budget, stale data, and malformed input.
- Keep everyday copy focused on meaning: “Sources need updating” or “Showing
  cached records from July 13.” Technical IDs/stack traces belong in diagnostics.
- Support keyboard navigation, associated form labels, visible focus, announced
  status/errors, accessible tables, and a usable narrow-screen layout.
- Provide explicit source and property pagination; do not label the displayed
  five rows as the complete set when more results exist.
- Escape external text and restrict citation link schemes. Render model output
  through a safe formatting path rather than trusting returned HTML.

Acceptance: a browser end-to-end test completes setup, asks a question, opens
evidence, disambiguates a property, pages results, retries a failed job, and
changes a budget without opening API documentation or a database tool.

### F10. Usage, budgets, deadlines, and cancellation

#### Cost accounting

- Use one append-only local usage ledger for answer generation, query
  embeddings, corpus embeddings, property summaries, validation, evaluation,
  and debugging. API, CLI, and jobs share it.
- A ledger entry records operation ID, provider/model/profile, price snapshot,
  input/output usage where available, reserved estimate, settled estimate,
  status, timestamp, and originating job/request. It excludes question text.
- Atomically reserve estimated spend before a request; settle after reported
  usage. Concurrent requests share reservations, preventing admission based
  only on already completed answer logs.
- Reservations, settlements, and corrections are separate linked events;
  derived balances may change, but existing accounting events are not rewritten.
- Include system prompts, questions, evidence metadata, and output limits in
  token estimates. Use the profile's tokenizer when available; otherwise
  disclose a conservative estimate.
- Capture prices as part of the profile with source/date. Changing today's
  prices never retroactively reprices old ledger entries.
- A timeout, cancellation, or lost response may still be billable. Record
  uncertain usage and retain a conservative accounting entry rather than
  declaring the request free.
- Scope budgets to this installation's activity. They cannot see or cap spending
  from the same key in other apps; provider-reported billing remains authoritative.
- Query/history deletion never resets cost accounting. Budget adjustments
  are explicit and do not delete the ledger.

#### Defaults and controls

- Default local monthly estimated-spend limit: USD 15, including all paid
  operation types in this application; user editable during setup.
- Before bulk embeddings/evaluation, show estimated request volume and cost
  and enforce an operation-specific ceiling. Noninteractive jobs require an
  explicit ceiling when there is no saved approval for that operation.
- Unknown pricing blocks automatic paid batches until a price/ceiling is set;
  the user can deliberately choose unpriced manual requests with an unknown-cost
  label. Do not describe them as covered by a reliable currency cap.
- Default to at most two concurrent paid requests and one corpus-ingestion
  job per workspace; users may lower these values.
- Keep paid calls and property downloads optional during diagnostics.

#### Time limits

- Default total remote-answer deadline: 60 seconds, including retrieval,
  query embeddings, retries, and generation; advanced setting may change it.
- Default live-property deadline: 15 seconds per interactive page.
- Per-attempt timeouts and retry delays must fit the remaining total deadline.
- Cancellation stops new batches and requests promptly, preserves committed
  progress, and records any uncertain in-flight cost.
- Enforce deadlines in shared services used by `/query` and CLI commands;
  checking elapsed time only after all work finishes is insufficient.

#### Acceptance

- Simultaneous requests near a cap cannot all pass by ignoring reservations.
- Direct evaluator/debug/embedding calls cannot bypass the shared ledger.
- A resumed job bills only newly requested work; a lost remote response is
  explicitly represented as uncertain rather than silently refunded.
- Cancellation releases local capacity without claiming to reverse a provider
  charge that may already have occurred.

### F11. Local access and privacy

#### Access model

- Bind native `serve` to `127.0.0.1` by default; explicitly supported IPv6
  loopback may be used. Do not silently expose the service on the LAN.
- Remove mandatory email/admin-account creation from the local user's flow.
  Use a per-installation secret and short-lived local browser session instead.
- The launcher creates a one-use, short-expiry bootstrap token. Open it in a
  URL fragment, exchange it via a same-origin POST for an HttpOnly SameSite
  session cookie, and immediately clear the fragment. Never log the token.
- Provide an equivalent one-time local code for `--no-browser`. A loopback
  address by itself must not grant an unrelated browser origin access to keys,
  paid requests, job controls, or exports.
- Validate Host and Origin, deny wildcard CORS, and require the local session
  and CSRF protection on state-changing or paid operations. Reject DNS-rebinding
  and cross-origin attempts in tests. Keep sensitive GET responses session-gated.
- Regenerate short-lived bootstrap state on restart; persistent settings and
  data remain. Optional app-password locking may be added in L2.
- Native network binding outside loopback fails with an explanation that shared
  hosting is not an L1 supported profile. Legacy authenticated hosting can be
  retained separately during transition, with its existing security requirements.

#### Privacy and external data flow

| Destination | Data the application may send | Trigger |
| --- | --- | --- |
| Model provider | Questions, selected legal excerpts, and selected property records for requested summaries | User asks for a model-backed operation |
| Embedding provider | Installed legal chunks during indexing; query text for semantic search | Approved indexing or semantic search |
| NYC Open Data | Structured property identifiers/filters; configured source token | Live property lookup/refresh |
| Official legal publishers | Public document requests and ordinary HTTP metadata | Source installation/update |
| GitHub | Release/update requests and ordinary HTTP metadata | User-selected update check/download |

- Setup explains this table in plain language. Locally running the app does
  not make remote-model prompts stay on the computer.
- No maintainer telemetry, account linkage, cloud query history, or automatic
  upload of questions/errors.
- Query/answer transcript persistence is off by default. Minimal diagnostic
  metadata and usage counts are local; operational logs have a default 30-day
  retention. Usage ledger records needed for budget/reporting have a default
  12-month retention, independently configurable.
- With history disabled, interactive question, context, and answer payloads
  remain in memory and expire after 30 minutes of inactivity or process exit.
  Durable job records contain sanitized progress/accounting metadata, not
  transcripts. Do not put these payloads in browser local storage or backups.
  An interrupted interactive answer requires explicit resubmission; background
  public-source ingestion remains resumable from its committed checkpoints.
- Property cache metadata may reveal researched addresses. Include cache
  clearing and age/size settings in the privacy controls.
- Export is an explicit user action. A default diagnostic export omits
  credentials, session tokens, raw prompts, questions, addresses, full local
  paths, and provider response bodies.
- OS file permissions/credential storage protect local files as available;
  the app does not claim protection against other processes already running
  with the same user's privileges.

Acceptance: cross-origin and hostile Host tests cannot read local data or
trigger paid operations; browsing/settings/logging do not expose secrets;
offline mode produces no application-managed outbound traffic.

### F12. Maintenance, diagnostics, and exports

#### Durable jobs

- Job states: `queued`, `running`, `paused`, `cancel_requested`, `cancelled`,
  `failed`, and `succeeded`, with stage, progress, retryability, and last error.
- Identify operations by stable job/source/profile IDs. Only one job can
  mutate a particular target generation/module at a time.
- Persist progress after each atomic batch. On restart, a previously running
  resumable maintenance job becomes `paused` with reason `process_interrupted`
  and a resume action. An interactive answer without a persisted payload
  becomes `failed` with that reason and a resubmit action. Neither can remain
  indefinitely labeled as actively running; uncertain charges remain accounted.
- Source checks, downloads, parsing, embeddings, and activation have independent
  outcomes. `ingest-missing` is not an update mechanism for already installed
  sources; provide a real `corpus update` command.
- Default reminders: flag core sources unchecked for 30 days; app-update checks
  are manual until enabled. Optional checks while the app is open never cause
  automatic paid embedding work.
- The application cannot refresh while it is closed unless the user explicitly
  installs an OS-scheduled job. Provide documented CLI scheduling recipes in
  L2; no maintainer scheduler is required.

#### Application updates

- Show installed application/schema versions separately from corpus versions.
  A release check reports available versions and migration notes; it never
  executes downloaded code or changes the checkout automatically.
- Document a supported tagged-release update workflow: stop the app, check
  for uncommitted work, back up local data, select the release, synchronize
  locked dependencies, and run migration preflight before restart.
- Never overwrite a dirty checkout or reset a user's branch as an update step.
  Contributor installations use an explicitly separate development workflow.
- A code downgrade does not imply a schema downgrade. Document compatible
  versions and require a matching backup/new workspace where needed; preserve
  the previous code reference and backup until the upgrade is verified.

#### Diagnostics

- `doctor` is read-only by default and reports app/schema versions, Python and
  FTS5 support, disk space, data paths, generation readiness, embedding profile
  compatibility, credential presence, cache state, and interrupted jobs.
- `doctor --online` performs bounded public/provider capability checks, with
  any paid check separately selected and metered.
- `status` uses maintained summaries/indexed metadata and never requires a
  full scan of a bulk property dataset.
- `sources` shows readiness and last successful check per source; `corpus verify`
  checks all retained references, checksums, and citation invariants.
- Errors give a concise cause and next action; a local debug log contains
  sanitized technical details.

#### Exports and backup

- Research exports: Markdown and JSON containing the question/result selected
  by the user, cited excerpts, original links, generation, prompt/profile
  versions, source dates, and property fetch/completeness metadata.
- CSV exports cover selected property rows or validated aggregate results.
  Neutralize spreadsheet-formula injection in text fields.
- Exports describe their scope and are saved to a user-selected path.
- Backup includes consistent databases and manifests plus selected referenced
  artifacts. Secrets are excluded and require reconfiguration after restore.
- Restore stages, validates, and requires an explicit destination selection.
  It cannot overwrite another workspace without an explicit replace action.

### F13. Migration from the current project

#### Requirements

- Preserve the current uncommitted work when implementing the overhaul.
  Establish a reviewed, reproducible baseline before structural refactoring;
  the migration pair already applied remotely must be captured in that baseline.
- New setup never automatically adopts the checkout's existing `.env`, remote
  `DATABASE_URL`, S3 credentials, or remote provider configuration. Detect
  legacy configuration and offer explicit import into a named local workspace.
- Provide a read-only legacy export path that copies active legal content,
  provenance, compatible embeddings, and necessary artifacts into a neutral
  bundle. Exclude users, sessions, private logs, and the full HPD table by default.
- Source credentials are used only to read the selected legacy backend;
  exported bundles contain no credentials.
- Verify downloaded artifacts from S3 and rewrite storage references to
  portable manifest-relative paths. Preserve original public source URLs.
- Import into a new local workspace, validate counts/IDs/hashes and embedding
  dimensions/profile, and build FTS indexes. The legacy database is untouched.
- Retain all selected current-source coverage. Superseded versions are an
  optional explicit migration scope, reported separately.
- Carry over verified compatible embeddings to avoid unnecessary costs.
  Imported model/profile metadata is validated before reuse.
- Existing API routes may remain as deprecated compatibility aliases during
  the transition. New pagination/status semantics are versioned as `/api/v1`.
  Document semantic differences rather than silently changing `count` meaning.
- Test infrastructure always overrides the database to a dedicated test
  workspace. An inherited real `DATABASE_URL` must never cause destructive
  fixture cleanup of a user database.

#### Acceptance

- The measured core baseline migrates with equivalent active chunks and evidence
  hashes; the initial remote database and S3 objects remain unchanged.
- Keys/accounts/logs do not appear in the export bundle.
- A malformed bundle fails before affecting an existing active workspace.
- A clean new clone can work without importing the developer's environment.

### F14. Documentation, testing, and distribution

- Publish one canonical README quickstart and links to Setup, Corpus/Coverage,
  Privacy/Data Flow, Costs, Troubleshooting, Updating, and Contributing.
- Mark the earlier hosted runbooks/plans as historical where their assumptions
  conflict; replace stale active instructions and limits.
- Include `.env.example` only as an advanced environment example. Ordinary
  users should not need to understand database URLs or storage credentials.
- Document what works without a key, without internet, with incomplete sources,
  and with an optional source token.
- Add LICENSE and dependency/source attribution documentation. Select a code
  license explicitly before public release; its choice does not determine
  redistribution rights for source artifacts.
- Git contains code, small permitted/synthetic fixtures, schemas, source recipes,
  and a lockfile. Databases, corpus downloads, secrets, and local state stay out.
- GitHub releases may distribute validated optional small content assets;
  publication checks must verify current platform limits. GitHub recommends
  releases for large binaries and does not treat Git as a database distribution
  format. See [GitHub's file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).
- Clean-clone CI runs on supported OS targets with isolated local data, synthetic
  providers, and no user credentials. Cover package installation, migrations,
  unit/integration tests, lint, browser flows, and selected performance checks.
- Live-source contract checks and paid-model evaluations run separately with
  bounded budgets and explicit release/operator invocation. They are not
  implicit work performed by every contributor's test run.
- Release artifacts include app version, tested runtime/dependency information,
  supported profile, source manifest versions, benchmark conditions, migration
  notes, and known coverage limits.

### F15. Additional legal sources (L2)

Add modules through a stable contract: source manifest, acquisition adapter,
parser, citation normalization, provenance, update policy, and evaluation cases.
Candidate scope from the broader plan includes rent-stabilization statutes and
regulations, DHCR guidance, additional RPL provisions, and official tenant guides.

The Sources view supports installing/removing modules, previews approximate
size and embedding cost, and reports cross-source dependencies. Every module
declares what questions it can and cannot support. A new source increases
coverage only after parsing and evaluation pass; simply adding a URL does not
make that subject available for answers.

Acceptance: a contributor can add a fixture-backed module without changing
the main question/answer pipeline; users can install it without replacing the
core workspace or other modules.

### F16. Optional container distribution (L2)

- One app container with a persistent data volume; no required PostgreSQL/S3
  sidecars. Publish only to a loopback host port by default.
- Build from the committed lockfile, include package assets, and run as an
  unprivileged container user where supported.
- Document environment/secret-file credentials and first-run local access
  bootstrap. Never bake keys into an image.
- State that binding inside the container differs from binding the host port;
  test that the supported Compose configuration does not expose the app to LAN.
- A container restart/rebuild preserves the mounted workspace. Volume deletion
  remains an explicit user action.

### F17. Saved history and local models (L2)

- Saved history is opt-in, configurable by retention, and supports individual
  or complete deletion without deleting usage accounting.
- Saved results retain evidence excerpts and generation references so pruning
  does not silently break them; show when original artifacts are no longer
  retained. Re-running a question creates a new result, not a rewritten record.
- Local model/embedding endpoints use the same provider contracts and offline
  controls. Publish a tested local profile separately from the remote profile;
  do not claim equivalent answer quality without evaluation.
- Fully offline new answers require a local answer model and local query
  embedding capability, or an explicit keyword-only retrieval profile.

## 6. Proposed data and service contracts

### 6.1 Principal records

| Record | Essential fields/invariants |
| --- | --- |
| Source module | Stable slug, publisher, scope, URLs, jurisdiction, adapter/parser versions, permission metadata, active flag |
| Source version | Content hash, artifact references, fetched/checked dates, known effective dates, validation state |
| Corpus generation | Immutable ID, source-version membership, profile ID, readiness, validation report; one active pointer |
| Chunk/evidence | Stable content identity, section path, citation/title, text hash, source version; immutable when referenced |
| Embedding profile | Provider/model, dimension, normalization/preprocessing, tokenizer policy, index format/version |
| Embedding | Chunk-content identity + complete embedding-profile identity, dimension, vector bytes, checksum |
| Model profile | Answer model, prompt version, context/output policy, provider capabilities, price snapshot/reference |
| Property response cache | Typed request key, resolved identity, connector version, records/artifact references, timestamps, completeness/cursor |
| Job | Type, target, state, stage, progress, lease/heartbeat, resume cursor, sanitized error, reserved spend; interactive payloads are memory-only by default |
| Usage event | Operation/profile, reserved/settled/uncertain usage, price snapshot, status, time; no raw prompt required |
| Optional snapshot | Dataset/schema identity, acquisition interval, row count, partitions/indexes, cursor provenance, completion and reconciliation status |

State names exposed by APIs must be enums/schema-validated values rather than
free-form strings. Dates use explicit timezone semantics. Unknown dates/counts
are `null` with explanatory status, never invented timestamps or zeroes.

### 6.2 CLI surface

All commands are proposed. Mutating/paid commands describe their effects;
status/inspect/doctor commands are read-only unless a flag explicitly says otherwise.

| Command | Responsibility |
| --- | --- |
| `nyc-housing setup` | Initialize workspace, credentials, and core-install workflow |
| `nyc-housing serve [--port N] [--no-browser]` | Start local UI/API |
| `nyc-housing status [--json]` | Active readiness, job, cache, and spend summary |
| `nyc-housing doctor [--online] [--json]` | Environment/capability diagnostics |
| `nyc-housing sources [--json]` | Installed/available modules and per-source freshness |
| `nyc-housing corpus install core [--text-only] [--max-cost-usd N]` | Install official core sources and optional embeddings |
| `nyc-housing corpus update [--source SLUG] [--max-cost-usd N]` | Check and stage source refreshes; report cost before embeddings |
| `nyc-housing corpus verify [--generation ID]` | Verify checksums, evidence references, and readiness |
| `nyc-housing corpus import PATH` | Validate/import a canonical content bundle |
| `nyc-housing corpus activate ID` | Select a validated generation with explicit partial-state handling |
| `nyc-housing corpus rollback` | Activate the previous retained validated generation |
| `nyc-housing jobs list/resume/cancel` | Inspect/control durable jobs by ID |
| `nyc-housing usage [--json]` | Usage, reservations, uncertain charges, limits |
| `nyc-housing evaluate [--offline] [--max-cost-usd N]` | Run isolated deterministic or explicit paid evaluation |
| `nyc-housing debug answer --question TEXT` | Inspect generation/retrieval/answer; paid calls use the ledger |
| `nyc-housing backup/restore` | Consistent workspace backup and validated restore |
| `nyc-housing migrate export-legacy` | Explicit read-only export from a selected legacy connection |
| `nyc-housing data install/update/status/remove hpd` | L2 bulk-extension lifecycle |

Shared options: `--data-dir`, `--config`, `--offline`, machine-readable output
where useful, and documented stable nonzero exit categories for invalid config,
unavailable dependency, exceeded budget, interrupted job, and failed validation.
Never accept API keys as command-line arguments visible in shell history or
process listings.

### 6.3 API surface

Use `/api/v1` for the new contracts; existing endpoints can delegate through
documented compatibility adapters during migration.

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/local-session` | Exchange one-use launch token; no arbitrary session creation |
| `GET /api/v1/status` | Workspace capabilities and readiness |
| `POST /api/v1/search` | Retrieval-only results with generation/method metadata |
| `POST /api/v1/query` | Routed question; immediate clarification/evidence or tracked asynchronous answer job |
| `POST /api/v1/properties/search` | Typed property lookup with identity, pagination, provenance |
| `POST /api/v1/properties/summarize` | Bounded, metered summary of an identified result set |
| `GET /api/v1/sources` | Per-module coverage/version/freshness/readiness |
| `POST /api/v1/corpus/jobs` | Create install/update/index job |
| `GET /api/v1/jobs/{id}` | Job stage, progress, output, error, and retryability |
| `POST /api/v1/jobs/{id}/cancel` | Request cancellation |
| `GET/PATCH /api/v1/settings` | Redacted settings and validated changes |
| `POST /api/v1/credentials` | Write-only set/replace credential with explicit storage choice |
| `GET /api/v1/usage` | Local cost accounting |
| `POST /api/v1/exports` | Create a specifically scoped local export |

Answer generation returns a job ID for work that can outlive an interactive
request, with status polling initially sufficient; streaming can be added later.
Short retrieval/property responses remain synchronous within their deadlines.
Job progress/results share the same local session protection as requests.
Tracking survives browser disconnection, not necessarily process restart:
interactive payload retention follows F11, while maintenance jobs resume under
F12. An expired answer payload returns an explicit expired-result state, not
an empty answer or an automatic paid rerun.

Each research response includes `status`, `mode`, `coverage`, `warnings`,
`provenance`, and either results or a job/clarification reference. Legal results
carry generation/profile IDs and evidence IDs. Property results carry resolved
identity, `returned_count`, nullable `total_count`, `has_more`, `next_cursor`,
fetch timestamps, cache state, and completeness. No consumer may interpret
`returned_count` as a dataset total.

## 7. Capacity and performance requirements

These are proposed release targets, not measurements of the unimplemented
local edition. Publish benchmark hardware, artifacts, profile dimensions,
dependency versions, warm/cold conditions, and network conditions with results.

### Reference installation

- Ordinary laptop, 8 GB system RAM, SSD, no GPU.
- Initial core target: 2 GB free space including staging allowance; measure
  actual downloads/dependencies and fail with a precise requirement if the
  selected source set exceeds the available space.
- macOS arm64/x86_64, Windows x86_64, and Linux x86_64 are intended native
  targets. Record exact tested OS/Python versions per release; a platform
  enters the supported list only after its install and browser tests pass.

| Measurement | L1 target |
| --- | --- |
| Idle app memory, current core | Below 500 MB RSS, excluding browser and external model service |
| App startup with installed corpus | Under 5 seconds, excluding browser launch |
| Keyword/exact search, 10,000 chunks | p95 below 500 ms on reference hardware |
| Local hybrid ranking, 10,000 x 1,536 vectors | p95 below 1 second, excluding remote query embedding |
| First rendered evidence | Shown as soon as local retrieval completes, without waiting for answer synthesis |
| Local cancellation acknowledgment | Within 1 second; already submitted provider work may still be billed |
| Live HPD page | Finish or return actionable timeout/cache state within the 15-second deadline |
| Core install | Target under 20 minutes on a documented broadband/provider benchmark, excluding externally blocked sources |

L1 benchmarks both the current corpus and a 10,000-chunk expansion fixture.
No fixed limit of 690 chunks is built into the design. For scale perspective,
10,000 vectors of 1,536 float32 values require about 58.6 MiB for vector values
alone; this is arithmetic, not an estimate of total application memory.

If a larger source pack exceeds documented memory/latency targets, report its
resource requirement and select a benchmarked optional index rather than
silently dropping sources or shrinking coverage. Bulk HPD storage/performance
is measured separately from legal retrieval.

## 8. Preservation of usefulness

The overhaul is accepted only if the storage/distribution change preserves
the following behavior:

| Existing or intended task | L1 commitment | Extension boundary |
| --- | --- | --- |
| Explain a supported legal provision | Same source scope, grounded answer, inspectable citations | Wider legal modules can be added independently |
| Exact citation lookup | Works offline without model credentials | Historical validity needs separate period coverage |
| Address/building HPD lookup | Live filtered data with cache and pagination | Complete offline lookup needs the bulk extension |
| Explain a property's returned violations | Explicit summary with record and law evidence | No inferred unobserved facts or legal eligibility |
| Citywide counts and comparisons | Explicitly unavailable in core; never approximated from cache | L2 structured analytics over a selected full snapshot |
| Case-law/precedent research | Explicit coverage limitation | Future reviewed case-law module |
| Reproducible research | Versioned local evidence and exports | Mutable live API results require captured response evidence |

Missing installation, temporarily inaccessible sources, and deliberate corpus
exclusions are distinct reasons for a limitation. None should be represented
as “the law contains no answer.”

## 9. Quality and release acceptance

### 9.1 Evaluation assets

- Convert the current five-item evaluator and 28-question legal review into a
  versioned fixture suite with expected relevant sections, accepted alternative
  citations, required propositions, missing-fact expectations, and refusal cases.
- Add at least 50 retrieval questions spanning core sources, synonyms, exact
  citations, multi-source questions, incorrect jurisdictions, ambiguity, and
  out-of-coverage topics. Label these independently of retrieval implementation.
- Add property fixtures for ambiguity, positive/negative results, pagination,
  duplicates, punctuation/hyphens, status filters, stale cache, and API errors.
- Keep model-generation tests separate from retrieval tests. Required keyword
  matching alone is insufficient evidence of legal correctness; accept equivalent
  wording and have a domain reviewer evaluate material claims/qualifications.
- Hold back paraphrases/cases from heuristic tuning to detect overfitting to
  the small current question set.

### 9.2 L1 gates

| Gate | Required evidence |
| --- | --- |
| Clean install | Native quickstart passes on every advertised platform with isolated data |
| Core completeness | Required source anchors verified; partial installs are clearly labeled |
| Retrieval parity | On identical artifacts/profiles, Recall@5 and MRR do not fall more than 2 percentage points below a frozen PostgreSQL baseline; documented core exact-citation cases rank correctly |
| Existing weak cases | Nonpayment notice, complaint follow-up, eCertification, personal scenarios, and Good Cause reviewed against the current source set |
| Citation integrity | All displayed citation IDs resolve to evidence actually supplied; substantive support assessed by reviewer |
| Legal review | All designated must-pass scenarios approved for the supported profile; no unresolved material correctness issue in that suite |
| Property behavior | Verified live connector contract and fixture tests; unavailable/partial/zero states remain distinct |
| Costs | All paid paths reserved/metered, concurrent caps tested, uncertain spend represented |
| Local access/privacy | Loopback/bootstrap/session/origin tests and no-key-leak tests pass |
| Updates | Interrupted download/parse/embedding/activation tests preserve last usable generation |
| Migration | Read-only legacy export/import validated on representative data; source environment unchanged |
| Documentation | README commands executed from a clean checkout; active instructions match defaults |
| Packaging | Lint/tests pass; lockfile and package assets verified; no personal state or secrets in release |

Parity with the old implementation is necessary but not sufficient: the July
review already recorded weaknesses. Those cases need explicit disposition even
if the new storage reproduces the old behavior.

Live-source checks establish current connector compatibility, not perpetual
availability. Re-run them when releasing adapter changes and identify the last
successful verification date in release notes.

## 10. Implementation workstreams and sequence

### Phase A: Baseline and packaging foundation

Scope: F01 foundation, F03 foundation, F13 safeguards, F14 foundation.

- Reconcile current work, capture migrations and review fixtures in Git, fix
  the existing lint failures, and add an isolated test-database guard.
- Introduce console entry point, package assets, locked dependency groups,
  runtime/data-directory separation, and a coherent configuration profile.
- Establish exact active-corpus inventory and a frozen retrieval baseline.

Exit: clean installation can initialize a fresh SQLite workspace and start a
local source-only shell without touching existing remote resources.

### Phase B: Local corpus and retrieval

Scope: F03, F04, F05, core F12, legal portion of F13.

- Implement FTS5, profile-aware local vectors, generation staging/activation,
  source-level provenance/readiness, and resumable batch processing.
- Add official-source bootstrap and read-only legacy export/import.
- Prove current-core coverage, offline exact/keyword search, and retrieval parity.

Exit: a new user can obtain/search the core sources locally; an existing user
can migrate compatible evidence/embeddings into a separate workspace.

### Phase C: Credentials, costs, answers, and safe local access

Scope: F02, F06, F10, F11, Research/Settings portions of F09.

- Implement key setup, published provider profile, unified usage reservations,
  total deadlines, cancellation, local-session bootstrap, and evidence UI.
- Replace default raw transcript logging with privacy-preserving accounting.
- Run the expanded legal evaluation and resolve material weak cases.

Exit: a keyed user can ask, inspect, export, and cancel a cited answer with
budget enforcement shared by UI, API, CLI, and jobs.

### Phase D: Live property research

Scope: F07 and property UI/API contracts.

- Implement typed Socrata adapter, identity resolution, token handling, bounded
  pagination, cache provenance/completeness, and optional summaries.
- Verify selected API dialect/schema live and exercise failures with fixtures.

Exit: address-specific research works without the bulk database and cannot
confuse an outage, cache miss, partial page, or ambiguous building with zero
violations.

### Phase E: Maintenance and first local release

Scope: remaining F09/F12/F14, all L1 gates.

- Finish Sources/maintenance UI, backup/restore, redacted diagnostics, research
  exports, cross-platform CI, and clean-clone documentation.
- Publish release notes, supported profile, coverage/evaluation report, and
  migration instructions. Select the code license and record source decisions.

Exit: all L1 gates pass. Publishing is a distinct maintainer action after
review; writing this specification does not publish code or data.

### Phase F: Optional expansion

Scope: F08 and F15–F17.

- Benchmark and implement optional full-data storage/analytics.
- Add independently evaluated legal modules, container distribution, saved
  research, and a separately tested local-model profile as demand warrants.

Exit: each extension documents footprint, dependencies, scope, and its own
acceptance evidence without increasing the core installation requirements.

## 11. Mapping to existing code

| Existing area | Reuse | Required overhaul |
| --- | --- | --- |
| `app/main.py`, `app/web.py`, templates/static | FastAPI and minimal browser shell | Loopback launcher/session, setup and maintenance views |
| `app/core/config.py`, `app/db/session.py` | Validated settings, SQLAlchemy sessions | Explicit local profiles, portable paths, SQLite defaults, legacy config isolation |
| `app/ingestion/*`, source models | Official parsers, citation normalization, artifact hashes | Generation staging, immutable provenance, resumable updates, referenced-artifact retention |
| `app/retrieval/*` | Citation/filter logic and hybrid pipeline | Production FTS5, reusable query embedding, profile-aware vectors and quality parity |
| `app/answer/*` | Provider abstraction, prompts, citation constraints | Evidence mapping, metered jobs, deadlines, qualified partial states |
| `app/hpd/search.py`, `app/query_routing/*` | Typed lookup concepts and routing | Live/cache repository, disambiguation, pagination/completeness |
| `app/ingestion/hpd_violations.py` | Optional bulk importer foundation | Keep out of setup; fix watermark/resume/reconciliation before L2 |
| `app/limits/*`, log models | Existing budget/error concepts | Independent ledger with reservations and all paid callers covered |
| `app/auth/*` | Legacy authenticated mode during transition | Single-user local access bootstrap; no required admin signup |
| `app/cli/*` | Ingestion/debug/evaluation semantics | Unified entry point, shared services, privacy-safe diagnostics |
| Migrations and tests | Existing migration history and fixtures | SQLite-specific indexes/migrations, real migration tests, non-destructive isolation |
| README, runbooks, Dockerfile | Existing operational knowledge | Local quickstart; archived hosted assumptions; container changes in L2 |

## 12. Decisions to finalize during implementation

These do not prevent implementing the architecture. Their defaults keep scope
concrete, and changing them requires documenting the impact.

| Decision | Proposed default | When it must be settled |
| --- | --- | --- |
| Exact remote model profile and prices | Pin a tested economical profile; preserve current configuration as migration input | Before paid acceptance evaluation and release |
| Prebuilt legal corpus assets | Official-source download is default; publish content only where redistribution is established | Before publishing any content asset |
| Code license | Explicit maintainer choice with dependency compatibility checked | Before public GitHub release |
| Bulk analytical storage engine | Benchmark DuckDB/Parquet versus indexed SQLite; isolate as an extra | Before F08 implementation is committed to a format |
| Platform support details | Native macOS/Windows/Linux targets; advertise only tested versions | Before release support matrix is published |
| Anonymous HPD API access | Capability-test chosen SODA2.1 endpoint; support user app token | Before F07 live acceptance |
| New legal modules | Prioritize by unmet research questions and available official evidence | After core quality gates pass |

The major design choices above preserve the project's capacity to grow. Local
distribution changes who runs the application and how data is acquired; the
coverage manifest, retrieval evaluation, and optional modules define how much
knowledge it actually provides.

## 13. Technical references checked for this specification

Checked September 14, 2026. These document external capabilities, while the
feature behavior, defaults, and targets above are proposed project decisions.

- [SQLite FTS5](https://www.sqlite.org/fts5.html): full-text indexing, column
  weighting, and BM25 ranking semantics used in F05.
- [uv project guide](https://docs.astral.sh/uv/guides/projects/): project scripts,
  Python selection, lockfiles, and package workflow underlying F01/F14.
- [Socrata application tokens](https://dev.socrata.com/docs/app-tokens.html):
  token identification and request throttling considerations in F07.
- [Socrata SODA3 announcement](https://support.socrata.com/hc/en-us/articles/34730618169623-Introducing-the-new-SODA3-API):
  API-version-specific identification requirements relevant to F07.
- [Official HPD dataset](https://data.cityofnewyork.us/Housing-Development/Housing-Maintenance-Code-Violations/wvxf-dwi5):
  target dataset identity; field/API access remains a release contract test.
- [GitHub large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github):
  repository versus release-asset distribution considerations in F14.
