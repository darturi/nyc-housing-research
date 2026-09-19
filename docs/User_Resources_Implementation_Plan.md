# User-added resources: implementation plan

Status: proposed implementation; this document does not claim the feature exists.

Date: 2026-09-18.

Target: the current local SQLite/FastAPI/browser application. The legacy hosted
PostgreSQL application is outside this change.

## 1. Goal and recommended first release

Allow a person to add documents to their local workspace, search their contents,
and optionally use them as cited evidence in generated answers. Preserve the
distinction between the packaged official collection and material the user adds.

The recommended first release supports text-based PDF, UTF-8 `.txt`, and `.md`
files. Markdown is treated as source text, never executed or rendered as HTML.
One uploaded file becomes one resource containing one document and many chunks.
Resources belong to the local workspace; no accounts or sharing service are needed.

### Included

- Add a resource through the Sources screen or CLI.
- Enter a title, optional publisher, document category, jurisdiction, and optional
  original HTTPS URL. Default the title from the filename.
- Parse locally and show progress, readable errors, and a sample of extracted text.
- Search uploaded material immediately without an API key.
- Filter research to official sources, user resources, all sources, or one resource.
- Show document/page or paragraph references in results, evidence, and exports.
- Replace a file, edit descriptive metadata, remove from active research, and restore.
- Use selected resources in model answers and semantic indexing when the user
  enables that capability for the resource.
- Preserve resources through restart, core updates, backup, and restore.

### Deferred

- Fetching arbitrary URLs, website crawling, scheduled refresh, or authenticated sites.
- OCR for scanned/image-only PDFs, image uploads, DOCX, spreadsheets, and archives.
- Batch folders, drag-and-drop collections, connector integrations, or shared libraries.
- Automated legal-authority verification, conflict resolution, or legal citation
  extraction for arbitrary case law and statutes.
- Permanent erasure across retained generations, backups, and research exports.
  The first release labels this action **Remove from research** and explains retention.

A URL entered with a file is provenance metadata; it does not trigger a download.
These boundaries keep the first release implementable with the existing stack.

## 2. Relevant current architecture

| Existing area | Reuse and required change |
| --- | --- |
| `app/corpus/manifests.py` | Loads only the packaged `core.json`. Add a registry abstraction that also reads local resource definitions; keep required core membership separate. |
| `app/corpus/service.py` | Already stores artifacts, versions, documents, chunks, FTS membership, and atomic generations. Unknown slugs are rejected, parser dispatch is source-specific, and source status enumerates only core manifests. Extend these boundaries. |
| `app/storage/schema.py` | Generic source/document/chunk tables are usable, but origin, upload metadata, page locators, and model-use policy need explicit representation. |
| `app/storage/database.py`, `app/cli/main.py` | Local schema version is 1. Initialization rejects incompatible versions; migration currently provides only preflight. Implement the first real local migration. |
| `app/corpus/indexing.py`, `app/answer/local.py` | Indexing assumes every active chunk is eligible; answer semantic readiness requires complete coverage. Support policy-filtered, partial vector coverage. |
| `app/jobs/service.py`, `app/jobs/maintenance.py` | Reuse durable progress, leases, cancellation, and resumable jobs. Preserve the shared corpus writer target. |
| `app/retrieval/local.py` | Reuse exact/keyword/vector search and filters. Add origin and model-use filtering, stable generation selection, and locators. |
| `app/local_app.py`, `app/templates/local.html`, `app/static/local.js` | Add upload/resource endpoints and extend the existing Sources screen. The JS request helper currently forces JSON, and source links assume HTTPS. |
| `app/maintenance/backup.py`, `app/corpus/bundle.py` | Backups copy all corpus records/artifacts; corpus bundles also export whole generations. Define custom-resource inclusion and format compatibility explicitly. |
| `app/answer/local.py`, `app/exporting/service.py` | Carry upload origin and document references through evidence, prompts, streamed results, and exports. Replace blanket claims that all evidence is public/official. |

The existing `corpus import-artifact` command repairs a known official module. It
does not register arbitrary documents and should retain its current purpose.

## 3. Product behavior

### 3.1 Add and inspect

1. Open Sources and choose **Add resource** in a **My resources** section.
2. Choose a supported file. Enter a title and optional descriptive fields.
3. A single optional control, **Allow this resource in model features**, explains
   that indexing sends document passages and answers send selected excerpts to
   the configured provider. It defaults off; local keyword search always works.
4. Choose **Add resource**. Upload, extraction, chunking, and keyword indexing are local
   operations. Adding a resource never automatically starts a paid embedding job.
5. Show upload/extraction progress and then its searchable status, page/chunk
   counts, extraction warnings, and an excerpt preview.
6. If model use is enabled, the existing estimate/build flow can process it.

Use the word **Added**, not **Checked**, for upload timestamps. An import timestamp
is not proof of publication date, effective date, freshness, or authenticity.

### 3.2 Research scope and evidence

- Default research remains **Official sources**. Users can deliberately select
  **My resources**, **All sources**, or an individual installed source.
- For uploaded results show `User-provided · Document title · PDF p. 12` or a
  paragraph range, plus category and optional original URL.
- A file without a public URL remains fully usable. **View excerpt** opens an
  authenticated local text view; **Download original** downloads the original.
- User-entered categories such as statute, guidance, decision, lease, notes, or
  other describe content. They do not promote the resource to official authority.
- When only uploaded evidence supports a response, describe what the document
  says. Do not describe it as verified current law.
- When a selected resource disallows model use, keep local search available and
  explain that model features are off for it. Enforce this on the backend too.
- For an all-sources model answer, disclose how many resources were excluded by
  model-use policy; only eligible evidence may enter the prompt.

### 3.3 Manage resources

- **Edit details:** create a metadata revision so old evidence keeps its original
  title/provenance. Reuse extracted content and vectors where identities match.
- **Replace file:** stage the replacement, then atomically activate it. A failed
  replacement leaves the existing version searchable.
- **Remove from research:** publish a generation that excludes the resource;
  retain its history and bytes for recovery. Explain this in the action text.
- **Restore to research:** publish a generation including a selected retained
  version. Show which version will return.
- Corpus rollback restores historical membership, so it can bring removed
  resources back. Preview the membership change before rollback.
- Turning model use off takes effect for subsequent provider dispatches and
  vector selection, including after rollback. Previously sent requests cannot be
  recalled; retained vectors are not automatically erased.

The UI should show official installation coverage and custom resource counts
separately. A workspace containing only uploaded resources can search them even
when none of the five core modules is installed.

## 4. Storage and migration design

### 4.1 Extend the corpus schema to version 2

Keep the state schema at version 1 unless implementation proves a new state table
is necessary. Reuse jobs and their existing JSON resume state.

| Record | Proposed additions or contract |
| --- | --- |
| `source_modules` | `origin` (`core`, `user`, `imported`), `acquisition_kind` (`managed_download`, `upload`, `bundle`), and `model_use_allowed`. User identifiers are generated UUIDs with a reserved `user-` slug prefix. |
| `source_versions` | Versioned `provenance_json` containing title, optional publisher/jurisdiction/original URL, category, original basename, media type, byte size, imported time, extraction statistics/warnings, and metadata revision. Include metadata identity in version uniqueness; content hash alone cannot distinguish metadata edits. |
| `chunks` | `locator_json` with physical PDF page, optional printed page label, paragraph offsets/range, heading path, and chunk ordinal. Keep a formal legal citation separate from a document locator. |
| `source_modules` / `documents` | Make their `source_url` fields nullable for uploads without a public URL; the optional original URL also appears in versioned provenance. Do not invent HTTPS links or store machine-specific `file://` paths. |
| New `corpus_operations` | Unique operation/job ID plus operation type, source/version/generation result. Commit this receipt with the corpus mutation to make interrupted-job recovery idempotent. |

`origin` is assigned by application-controlled ingestion paths. Users cannot
submit it in resource metadata. Incoming bundle fields cannot confer core status
or overwrite an existing core module merely by supplying its slug.

Store model-use policy at the resource level, independently of historical
generation membership. Rollback must not revert a user's current policy choice.
For ordinary uploaded documents, keep `source_type=reference`; use the descriptive
category for presentation rather than triggering the official-law parser rules.

Use immutable version metadata as the source of displayed historical provenance.
Any module-level title fields become current-library conveniences, not the
authoritative metadata for old citations.

### 4.2 Artifact storage

- Store originals under generated IDs and content hashes inside the workspace's
  artifact directory. Never use the supplied filename as a directory or path.
- Keep normalized extraction/chunk output as a versioned derivative when useful
  for retries; mark parser name/version and content hash explicitly.
- Use an owner-restricted staging directory for incomplete imports. A private
  staging manifest may hold the pending descriptive metadata; durable jobs store
  opaque staging IDs, hashes, and progress rather than full text or original paths.
- Copy the selected file into workspace storage. Later modification/deletion of
  the user's original file must not change an installed version.
- Sweep abandoned staging files after a proposed 24-hour TTL, excluding files
  referenced by active or resumable jobs. Terminal cancellation cleans up promptly.

### 4.3 Migration and compatibility

1. Add a local migration registry and a concrete corpus `1 -> 2` migration. Do not
   reuse or alter the historical hosted Alembic revision chain.
2. Require the local server to be stopped and acquire an exclusive workspace
   maintenance lock. Create and verify a consistent backup before migration.
   Have normal server/CLI processes participate in the same lock protocol so
   this excludes new readers/writers as well as already running jobs.
3. Add/backfill columns and rebuild SQLite tables only where constraints require
   it. Preserve source/chunk IDs, vectors, active generation, and FTS membership.
4. Backfill known existing core records from the packaged registry only after
   validating their expected identity/structure. Unknown bundle records become
   `imported`, not official; default their model-use policy off.
5. Update schema metadata in the same transaction as structural changes. Treat
   `workspace.json` as a recoverable mirror, written atomically after commit;
   recover a crash between DB commit and manifest publication on next startup.
6. Add `nyc-housing migrate apply` and extend preflight to report this available
   path. All normal open/serve paths must validate compatible schemas before use.
7. Refactor backup/restore's hard-coded version-1 checks and manifest values.
   Restore older backups into a new destination, then use the same migration path.
8. Keep pre-migration backups usable with the previous app. Downgrade means
   restoring a compatible backup, not editing version numbers.

Acceptance: a populated v1 fixture migrates without changing official search
results; injected failure leaves either a usable v1 store or a fully committed
v2 store with recoverable metadata. Unknown future schemas are rejected.

## 5. Import, extraction, and publication

### 5.1 Bounded inputs

Proposed initial limits, to be checked against representative fixtures:

- 25 MiB per upload; one file per import request.
- 500 PDF pages; 2 million extracted characters; 5,000 chunks per document.
- 60-second parser deadline and one parsing worker per workspace.
- 200 custom resources and 1 GiB of retained custom originals per workspace.
  Count retained versions and staged reservations when enforcing storage limits.

These are application defaults, not limits of PDF or SQLite. Report the actual
limit and a corrective action when an import is rejected. Measure local retrieval
and memory at the supported corpus size before settling final release limits.

Apply a streaming request-body limit before multipart parsing/spooling, including
requests without `Content-Length`. Validate extension, media type, signature, and
decoding consistently. Reject archives, binary text, malformed PDFs, encrypted
PDFs, and unsupported formats with stable error codes. Do not silently replace
invalid UTF-8 bytes.

Use the existing `pypdf` dependency for text PDFs, preserving page boundaries.
Run parsing in a terminable subprocess with bounded output so cancellation or a
timeout can stop it. A process alone is not a memory/security sandbox: verify
portable memory limiting or document the residual bound during the parser spike.
Never execute PDF actions, embedded content, Markdown HTML, or external references.

### 5.2 Generic chunking and citation references

- Extract PDF text page by page; preserve 1-based physical page numbers even if
  printed labels differ. Never fabricate a page for text files.
- Normalize whitespace without losing paragraph/page mappings. Bound each chunk
  to approximately 2,500 characters, hard cap 4,000, with limited overlap within
  its page/section. Keep text within the answer service's current excerpt ceiling.
- Split long paragraphs deterministically; record the split and original offset.
- Treat headings as optional organization, not a prerequisite for valid input.
- Keep PDF chunks on one page initially; support multiple chunks per page.
- Report pages with no extractable text. Reject wholly image-only PDFs and label
  partial extraction when some pages were skipped; do not report full coverage.
- Present locators such as `Lease.pdf, PDF p. 4` or `Tenant notes, paragraphs 8–10`.
- A citation mentioned inside a custom document is a reference, not proof that
  this passage is the cited statute. Do not rank it as a primary exact-law match.

Use deterministic chunk identifiers derived from version, parser, and locator.
An identical import retry returns the existing result. Adding the same bytes as
a new resource returns a duplicate indication; replacing a resource with unchanged
bytes/metadata is a no-op. Changed parser or metadata versions remain traceable.

### 5.3 Durable job lifecycle

`Receive -> validate -> extract -> chunk -> stage -> activate -> complete`

- Enqueue only after the complete upload is persisted and validated at the byte
  level. An interrupted transfer creates no searchable resource.
- Extend corpus job dispatch for resource add/replace/remove/restore operations.
  Use the same existing corpus writer target as core updates and indexing;
  per-resource locks alone would allow lost updates to the active generation.
- Publish artifacts atomically, then commit versions, generation membership,
  FTS rows, and the operation receipt in a single corpus transaction.
- Check cancellation before publication. After activation, report success even
  if a late cancellation arrived; do not claim a committed mutation was cancelled.
- If the corpus commit succeeds but the state-database job update fails, resume
  from the operation receipt and mark the job complete without importing twice.
- On pre-commit failure, leave the prior generation usable and retain only staging
  needed for a deliberate retry. Clean genuinely unreferenced published artifacts.
- Return an actionable conflict when another corpus writer is active. The UI can
  retry without silently applying against an outdated source version.

## 6. Registry and generation changes

Introduce `SourceRegistry` with distinct operations for packaged core manifests,
installed resource definitions, and required core coverage. Avoid simply appending
custom definitions to `_manifests`: that would make every upload a required core
module and would route core download jobs to local uploads.

Factor a generation builder out of `CorpusService`:

- Begin from the current active mapping of source to version.
- Add/replace or exclude only the requested resource.
- Compute missing core sources using the packaged core registry alone.
- Preserve unrelated core and custom source versions.
- Build FTS membership and copy compatible embedding membership/profile for
  unchanged chunks. The current blanket reset to `embedding_ready=False` must go.
- Add chunks without vectors as text-ready. Do not discard the vectors or
  semantic availability of unchanged official sources.
- For metadata-only revisions that change version/chunk IDs, reuse vectors only
  after checking identical normalized chunk text, preprocessing, and full profile
  identity. Copy the vector association to the new IDs; a changed title alone
  should not require another embedding call when titles are not embedded.
- Retain the previous generation and switch the pointer transactionally.
- Reject stale replacement/metadata edits using the expected source version ID.

Add/replace/remove/restore and core update must all use this shared builder.
Removing the final resource is valid: show an empty searchable collection and
explicit missing-core coverage rather than an unexplained failure.

Keep format validation and authority separate. A valid upload means it parsed
and has intact provenance; it does not mean its legal claims were verified.

## 7. Retrieval, model policy, and answers

### 7.1 Search and generation consistency

Add an origin/scope filter to local retrieval and propagate it through search,
query routing, answer jobs, debug output, and export provenance. Apply the filter
in exact, keyword, hinted-source, and vector candidate queries before ranking.
Apply SQL filters before vector selection too; filtering only the displayed
results could leak an excluded excerpt into an answer.

Capture one generation ID for the entire answer operation: readiness checks,
query embedding decision, retrieval, coverage, and returned provenance must use
that generation. A core update or upload committed mid-answer must not mix them.
Recheck the current model-use policy before dispatching any document text.

For mixed research, retain reliable retrieval of core authority and distinguish
referenced citations from a source's own formal citation. Use regression cases
with distracting user documents rather than an unmeasured global ranking rewrite.

### 7.2 Partial semantic coverage

- Compute semantic readiness for the chosen generation, profile, scope, and
  model-eligible chunk set, rather than requiring every active chunk to have a vector.
- Reuse vectors for unchanged chunks; keyword-search all scope-eligible text and
  vector-search only compatible indexed chunks.
- Report vector coverage counts and a `partial` status when appropriate.
- A core-only answer should retain semantic retrieval after adding an unindexed
  user document. A user-only collection without vectors uses keyword retrieval.
- Model-ineligible custom text is excluded from embedding estimates and batches.
- Bind paid index approval to generation, profile, eligible version IDs, and cost
  ceiling. Reject a changed workset and request a fresh estimate through the
  existing workflow instead of silently including newly uploaded documents.
- Revocation before a batch prevents that batch; already completed spend remains
  recorded. Enabling model use does not automatically start indexing.

### 7.3 Evidence and prompts

Extend result/evidence contracts with origin, category, source version, content
hash, and locator. Preserve `[E1]`-style inline markers and the existing rule that
only supplied evidence markers are accepted.

Include origin and document locator in each prompt evidence block. Treat all
uploaded fields, including titles and publisher names, as quoted data. Continue
the current instruction that source text is not executable model instructions.
Bound and escape structured evidence fields to prevent forged evidence markers
or metadata from impersonating system instructions.

Replace `Installed public sources` with a scope-aware description. Do not convert
an uploaded document date to an effective legal date. Keep historical-coverage
limitations explicit, and do not let a user-entered date silently certify law
in force for a past period.

Update browser evidence panels, streaming payloads, CLI JSON, debug views, and
Markdown/JSON exports together. Optional HTTPS original links and authenticated
local excerpt links are separate typed fields; do not loosen the renderer to
accept arbitrary URLs.

## 8. API and CLI contracts

The route names below are proposed. Put shared business logic in services used
by both HTTP and CLI adapters; extract a small resource router if it improves
readability of the already large `local_app.py`.

| Operation | Proposed endpoint | Behavior |
| --- | --- | --- |
| Library | `GET /api/v1/sources` | Extend existing response with origin, custom-resource state, core coverage, and model policy; include removed custom resources in a separate section. |
| Add | `POST /api/v1/resources` | One multipart file plus metadata; return `202` with stable resource/job IDs. Accept an idempotency key. |
| Inspect | `GET /api/v1/resources/{id}` | Version metadata, import status, extraction warnings, and bounded preview. |
| Edit details/policy | `PATCH /api/v1/resources/{id}` | Allowlisted fields, expected version; version descriptive edits, apply current model policy separately. |
| Replace | `POST /api/v1/resources/{id}/versions` | Multipart file plus expected current version; return `202`. |
| Remove from research | `POST /api/v1/resources/{id}/remove` | Publish exclusion generation; does not imply physical erasure. |
| Restore | `POST /api/v1/resources/{id}/restore` | Publish selected retained version, subject to current policy. |
| Read excerpt | `GET /api/v1/resources/{id}/versions/{version}/chunks/{chunk}` | Authenticated, bounded text/locator response; verify all IDs belong together. |
| Original file | `GET /api/v1/resources/{id}/versions/{version}/download` | Authenticated attachment, safe basename, no filesystem path parameter. |
| Progress/control | Existing `/api/v1/jobs/...` | Reuse progress, cancellation, resume, and error contracts. |

Reuse same-origin, session, CSRF, and no-store headers. Extend the JS API helper
to pass `FormData` without forcing `application/json`; the browser supplies the
multipart boundary. Add and lock `python-multipart` if using FastAPI multipart
handling. Enforce ingress limits before its parser starts buffering the request.

Use consistent errors: `413` for size, `415` for unsupported media, `422` for
invalid input, `409` for active-writer/version conflicts, and `404` for missing
resources. Asynchronous parse failures use job error codes such as
`pdf_password_required`, `pdf_no_extractable_text`, and `parser_timeout`.

Proposed CLI surface:

```text
nyc-housing resources add PATH --title TITLE [--allow-model-use] [--json]
nyc-housing resources list [--include-removed] [--json]
nyc-housing resources show ID [--json]
nyc-housing resources edit ID --title TITLE [--json]
nyc-housing resources model-use ID on|off [--json]
nyc-housing resources replace ID PATH [--json]
nyc-housing resources remove ID [--json]
nyc-housing resources restore ID [--version VERSION] [--json]
nyc-housing search QUERY --scope official|mine|all [--source SLUG]
```

Preserve `sources --json` and existing core-import commands. The CLI must copy
input bytes into staging, use the same validations, and never depend on a browser
process for job completion. Document the precedence of a specific source filter
over the default scope and reject contradictory explicit filters.

## 9. Recovery, export, and privacy behavior

- Workspace backups intentionally include custom documents and retained versions
  so recovery is complete. Make this clear in backup metadata/output; excluding
  credentials does not make a backup free of sensitive document contents.
- Adapt schema/backup readers to v1/v2 stores and add round-trip tests. Exclude
  incomplete staging; block backups while publication is active as today.
- Keep canonical corpus bundles scoped to the core collection in this release.
  Export a coherent core-only generation with matching chunks, citations, vectors,
  coverage, and a new identity if filtering a mixed generation. Do not silently
  publish custom documents via the existing whole-corpus export path.
- If there are no core sources to export, return a clear empty-scope error. Bundle
  import must use the shared generation builder to merge supported core versions
  into the current collection; it must not activate a core-only bundle in a way
  that unexpectedly drops the user's active resources.
- Update bundle format handling for the new fields. Read supported v1 bundles
  through an explicit adapter, validate provenance/origin, and reject unsupported
  newer formats clearly. Defer custom-resource bundle sharing.
- Diagnostics/logs contain opaque IDs, counts, types, hashes where appropriate,
  and safe error codes; omit uploaded text, titles, filenames, paths, and URLs
  from redacted support exports. Browser library views may display those fields
  within the authenticated local session.
- Existing explicit research exports may include the selected user excerpts and
  their origin/locators. Include document name/version/page as text so the export
  remains intelligible outside the original local session.
- Keep private text out of analytics, provider fallback paths, and auto-generated
  error strings. Exercise fake-provider tests that capture outbound payloads.
- Removed resources remain in retained generations and backups. A later purge
  feature must account for FTS, vectors, artifacts, rollback references, WAL,
  in-memory caches, and independent exports/backups before promising deletion.

## 10. Delivery work packages

Estimates are focused engineering days for one engineer familiar with this code,
including relevant tests. They are planning ranges, not elapsed-time promises.

| Package | Work and concrete output | Depends on | Effort |
| --- | --- | --- | --- |
| U01 — Contracts and parser spike | Finalize the above scope, make representative fixtures, measure extraction/limits, resolve portable worker termination, and lock API/data contracts. | None | 0.5–1 day |
| U02 — Migration and registry | Corpus v2 migration, upgrade/restore support, typed registry, versioned provenance, policy fields, and operation receipts. | U01 | 2–3 days |
| U03 — Import and lifecycle | Bounded PDF/text parsers, locators, staging, duplicate handling, add/replace/remove/restore services, durable job integration, shared generation builder. | U02 | 2–3 days |
| U04 — Research integration | Origin/model filters, generation-consistent answers, partial vector coverage, preserved embeddings, evidence/prompt/export propagation. | U03 | 2–3 days |
| U05 — Browser and CLI | Upload/manage UI, multipart endpoints/helper, progress/errors, source filters, local excerpt/download links, shared CLI commands. | U03; final integration with U04 | 2–3 days |
| U06 — Recovery and release verification | Backup/bundle/diagnostics changes, mixed-corpus regression evaluation, browser journey, failure injection, limits/performance checks, docs. | U02–U05 | 2–3 days |

Total: approximately **10.5–16 engineering days**, plus contingency for migration
or parser findings. Budget **roughly 2–4 calendar weeks** for a complete first
release with review and representative document testing. U04 and portions of
U05 can proceed independently once the service contracts settle, but extra people
do not remove the migration and final integration dependencies.

The earlier 5–10-day estimate fits a constrained working slice. Detailed inspection
adds first-time schema migration, partial semantic indexing, and recovery/export
compatibility, so the full release estimate is higher. Defer URL fetching and OCR
until this release is stable and estimate them separately.

### Milestones

1. **Local search slice:** migrated workspace; add text/PDF through CLI; search
   with locators; replace safely; core updates preserve user resources.
2. **Research slice:** policies enforced, partial vectors work, user-origin
   citations appear in generated answers and exports; official regressions pass.
3. **Usable release:** complete browser management, resilient job recovery,
   migration/backup tests, and end-to-end acceptance below.

Every package should land with its own failure-path tests. The final package
checks integration; it should not be the first time tests or docs are written.

## 11. Test matrix and release acceptance

| Area | Required evidence |
| --- | --- |
| Extraction | Text PDF, UTF-8 text, Markdown, multi-page content, repeated headings, long paragraphs, Unicode, mixed image/text pages, empty/corrupt/encrypted PDFs, invalid UTF-8, oversized/chunked requests, deadline/cancellation. |
| Provenance | Uploaded resources never become official through title/category/URL/slug; page/paragraph locators match originals; referenced statutes are not misrepresented as primary text; old evidence keeps versioned metadata. |
| Lifecycle | Successful add, byte-identical retry, duplicate indication, metadata edit, replacement, remove, restore, empty collection, unchanged core coverage, and rollback membership preview. |
| Atomicity/recovery | Disk-full, parse failure, commit failure, crash after corpus commit before job completion, lost upload connection, expired lease, restart/resume, and late cancellation; no half-active corpus or duplicate operation. |
| Concurrency | Core update vs upload/index/replace/remove; stale version edit; two concurrent uploads; backup vs publication; a running answer captures one generation. |
| Retrieval | Official fixtures unchanged in official scope; mine/all/individual filters hold in every candidate branch; custom-only corpus works; uploaded references do not crowd out primary exact citations. |
| Model policy | Default uploads cause zero provider calls; disallowed text never enters embeddings/prompts; changing policy and rolling back do not reauthorize it; allowed resources require the existing indexing cost approval. |
| Semantic coverage | Existing core vectors survive add/remove; new text is keyword-searchable before indexing; old vectors are reused; eligible partial indexes work; changed worksets invalidate estimates. |
| Local security | Session/CSRF checks, spoofed IDs and core slugs, path traversal, malicious metadata, HTML/script text, safe download names/links, bounded multipart parsing, redacted errors. |
| Portability/recovery | Populated v1 -> v2 migration; unknown schemas rejected; pre-migration failure recovery; v1/v2 backup restore; bundle format adapters and core-only export; Windows/macOS/Linux parser termination and filenames. |
| UI | Keyboard-accessible add/manage flow, correct focus/error announcements, progress/resume, unavailable model-use explanation, local-source evidence links, and no file contents persisted in browser storage. |
| Capacity | Representative mixed corpus up to proposed limits; measured upload memory, parser runtime, SQLite/FTS growth, vector cache use, search latency, and backup size. |

Use deterministic fixtures and fake providers for routine verification. Add
focused tests such as `test_resource_import.py`, `test_resource_lifecycle.py`,
`test_resource_policy.py`, and `test_local_schema_migration.py`, and extend the
existing local API/search/answer/indexing/backup/bundle/browser suites.

Run focused tests and lint as each package changes, then one full isolated suite
and browser journey for the integrated release. Reuse the existing evaluation
cases and add mixed-source cases; do not invent a passing domain-review result
from a successful technical test. A paid-provider check is optional and uses the
existing explicit cost workflow.

The feature is ready when a new user can add a text PDF, find a passage and its
correct page without a key, optionally enable model use and receive correctly
labeled evidence, replace/remove/restore the resource, update core sources, and
restart or restore a backup without losing it. Existing official-only research
must continue to pass its regression cases.

## 12. Expected file changes and documentation

Likely new modules (names are proposals, not existing implementation):

- `app/corpus/registry.py`: combined registry with separate core requirements.
- `app/corpus/resources.py`: resource metadata and lifecycle service.
- `app/corpus/resource_parsers.py`: bounded extraction/chunking and locators.
- `app/corpus/generations.py`: shared generation construction and membership.
- `app/jobs/resources.py`: import orchestration and restart recovery.
- `app/storage/migrations.py`: local migration dispatch and corpus v1 -> v2.
- `app/api/resources.py`: authenticated upload, management, excerpt, and download.

Extend the existing storage, corpus service/indexer, local retrieval/answer,
jobs, CLI, local app/template/JS/CSS, export, backup/bundle, and diagnostics modules.
Avoid a new frontend framework, hosted service, separate vector database, or
duplicate retrieval engine for this feature.

Update README, Setup, Coverage, Privacy and Data Flow, Updating, Troubleshooting,
Migration, Compatibility, and release acceptance evidence. Add a short resource
guide covering supported files, model use, page references, partial extraction,
replacement, retained removal, backup contents, and deferred formats.

If implementation reveals a material change to these recommended scope choices,
record the decision here before extending the feature. All phases above are
planned work; no production code or database has been changed by this document.
