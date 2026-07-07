# Phase 3: Source Registry and Ingestion Implementation

## Goal

Build the ingestion foundation for the MVP corpus. Phase 3 should create a
source registry, verify that each source is freely accessible, download and
hash raw source artifacts, store those artifacts, parse legal text into
documents/sections/chunks, normalize citations, load HPD violations into
relational tables, and record ingestion status and errors.

This phase prepares data for retrieval. It does not implement search,
embeddings, reranking, or LLM answer generation. Those belong to later
phases.

## MVP Source Scope

Ingest only the first-version corpus:

-   NYC Housing Maintenance Code from official public NYC sources
-   Multiple Dwelling Law from official public NY State sources
-   RPAPL from official public NY State sources
-   HPD public tenant/owner guidance
-   HPD violations from NYC Open Data

Every source must have:

-   Public source URL
-   Publisher
-   Access type
-   License or terms status
-   Retrieval timestamp
-   Version hash
-   Raw artifact location

Do not ingest paid legal databases, commercial summaries, proprietary
headnotes, paid citators, or any source whose terms do not allow the
intended use.

## Deliverables

-   Source registry schema and seed data
-   Source version tracking schema
-   Raw artifact storage abstraction
-   Ingestion run tracking
-   Document, section, chunk, and citation tables
-   HPD violation table
-   CLI ingestion commands
-   Source downloader framework
-   Legal text parser framework
-   HPD violation loader
-   Citation normalization utility
-   Idempotent ingestion behavior
-   Tests for source validation, hashing, parsing, and idempotency
-   README updates with ingestion commands
-   Work log entry after implementation

## Design Principles

### Free-Source Gate

No source should enter ingestion unless it passes a source-access check.
The check should confirm:

-   URL is public
-   Source is not paywalled
-   Source is not a commercial legal research database
-   License or terms status is recorded
-   Publisher is recorded
-   Intended use notes are recorded

This is not a final legal opinion. It is an auditable project control.

### Idempotency

Re-running ingestion must not duplicate sources, documents, chunks, or HPD
violations.

Use stable keys:

-   Source slug for source registry entries
-   Source version hash for downloaded artifacts
-   Document stable key for parsed source documents
-   Citation or hierarchy path for legal sections
-   Chunk content hash for chunks
-   External violation ID for HPD violations

### Traceability

Every chunk and violation row should be traceable back to:

-   Source
-   Source version
-   Source URL
-   Retrieval timestamp
-   Raw artifact location

### Small First

Start with one legal source and HPD violations before scaling to all MVP
sources. The right first milestone is one complete ingestion loop, not all
sources partially ingested.

## Suggested Files

``` text
app/
├── cli/
│   └── ingest.py
├── ingestion/
│   ├── __init__.py
│   ├── artifacts.py
│   ├── citations.py
│   ├── downloaders.py
│   ├── hpd_violations.py
│   ├── legal_text.py
│   ├── registry.py
│   └── runners.py
├── models/
│   ├── chunk.py
│   ├── citation.py
│   ├── document.py
│   ├── hpd_violation.py
│   ├── ingestion_run.py
│   ├── section.py
│   ├── source.py
│   └── source_version.py
└── schemas/
    └── ingestion.py
tests/
├── fixtures/
│   ├── hmc_sample.html
│   └── hpd_violations_sample.json
├── test_ingestion_citations.py
├── test_ingestion_hpd_violations.py
├── test_ingestion_registry.py
└── test_ingestion_legal_text.py
```

## Database Schema

### `sources`

Represents a logical source, such as the Housing Maintenance Code or HPD
violations dataset.

Columns:

-   `id`: UUID primary key
-   `slug`: unique stable identifier
-   `name`: display name
-   `source_type`: `law`, `guidance`, `dataset`
-   `publisher`: source publisher
-   `jurisdiction`: `NYC`, `NY`, or other
-   `source_url`: canonical public URL
-   `access_type`: for MVP, usually `public_web`, `public_api`, or
    `public_bulk_download`
-   `license_status`: short status such as `public_official`,
    `open_data`, `terms_reviewed`, or `unknown_review_required`
-   `terms_url`: nullable terms URL
-   `redistribution_allowed`: nullable boolean
-   `notes`: source-access notes
-   `is_active`: boolean
-   `created_at`: timestamp
-   `updated_at`: timestamp

Constraints:

-   Unique `slug`
-   Non-empty `source_url`
-   Non-empty `publisher`
-   Non-empty `license_status`

### `source_versions`

Represents one retrieved version of a source.

Columns:

-   `id`: UUID primary key
-   `source_id`: foreign key to `sources.id`
-   `retrieved_at`: timestamp
-   `source_url`: URL used for retrieval
-   `content_hash`: SHA-256 hash of raw artifact bytes
-   `artifact_uri`: local path or object storage URI
-   `content_type`: MIME type or best-known type
-   `byte_size`: raw artifact size
-   `effective_start`: nullable date
-   `effective_end`: nullable date
-   `is_current`: boolean
-   `created_at`: timestamp

Constraints:

-   Unique `(source_id, content_hash)`

### `ingestion_runs`

Tracks each ingestion attempt.

Columns:

-   `id`: UUID primary key
-   `source_id`: nullable foreign key
-   `source_version_id`: nullable foreign key
-   `run_type`: `registry_seed`, `download`, `parse`, `load_dataset`,
    `full_source`
-   `status`: `started`, `succeeded`, `failed`
-   `started_at`: timestamp
-   `finished_at`: nullable timestamp
-   `error_message`: nullable text
-   `records_created`: integer
-   `records_updated`: integer
-   `records_skipped`: integer

### `documents`

Represents a parsed document within a source version.

Columns:

-   `id`: UUID primary key
-   `source_id`: foreign key
-   `source_version_id`: foreign key
-   `document_key`: stable source-local key
-   `title`: document title
-   `document_type`: `statute`, `guidance`, `dataset_record_group`
-   `jurisdiction`: jurisdiction label
-   `source_url`: public URL
-   `created_at`: timestamp

Constraints:

-   Unique `(source_version_id, document_key)`

### `sections`

Represents structured legal hierarchy.

Columns:

-   `id`: UUID primary key
-   `document_id`: foreign key
-   `parent_section_id`: nullable self-reference
-   `section_key`: stable source-local key
-   `citation`: normalized citation if available
-   `title`: section title
-   `hierarchy_path`: machine-readable hierarchy path
-   `order_index`: integer
-   `text`: section text

Constraints:

-   Unique `(document_id, section_key)`

### `chunks`

Represents retrieval-ready text units. Embeddings are deferred to Phase 4.

Columns:

-   `id`: UUID primary key
-   `document_id`: foreign key
-   `section_id`: nullable foreign key
-   `source_id`: foreign key
-   `source_version_id`: foreign key
-   `chunk_key`: stable key
-   `chunk_type`: `section`, `subsection`, `guidance_heading`,
    `dataset_summary`
-   `citation`: nullable citation
-   `title`: nullable title
-   `text`: chunk text
-   `text_hash`: SHA-256 hash of normalized text
-   `order_index`: integer
-   `created_at`: timestamp

Constraints:

-   Unique `(source_version_id, chunk_key)`
-   Index on `citation`
-   Index on `text_hash`

### `citations`

Represents normalized citations extracted from sections/chunks.

Columns:

-   `id`: UUID primary key
-   `source_id`: foreign key
-   `document_id`: foreign key
-   `section_id`: nullable foreign key
-   `chunk_id`: nullable foreign key
-   `citation_text`: original citation text
-   `normalized_citation`: normalized citation
-   `citation_type`: `nyc_code`, `ny_law`, `rpapl`, `guidance`

Constraints:

-   Index on `normalized_citation`

### `hpd_violations`

Represents HPD violation records from NYC Open Data.

Columns:

-   `id`: UUID primary key
-   `source_id`: foreign key
-   `source_version_id`: foreign key
-   `external_id`: stable NYC Open Data violation ID
-   `building_id`: nullable
-   `registration_id`: nullable
-   `boro`: nullable
-   `house_number`: nullable
-   `street_name`: nullable
-   `zip_code`: nullable
-   `apartment`: nullable
-   `class`: nullable
-   `inspection_date`: nullable date
-   `approved_date`: nullable date
-   `original_certify_by_date`: nullable date
-   `original_correct_by_date`: nullable date
-   `new_certify_by_date`: nullable date
-   `new_correct_by_date`: nullable date
-   `certified_date`: nullable date
-   `order_number`: nullable
-   `nov_id`: nullable
-   `nov_description`: nullable text
-   `current_status`: nullable
-   `current_status_date`: nullable date
-   `raw_record`: JSON
-   `created_at`: timestamp
-   `updated_at`: timestamp

Constraints:

-   Unique `external_id`
-   Index on address fields
-   Index on `building_id`
-   Index on `registration_id`
-   Index on `current_status`

## Environment Variables

Add these to `.env.example`:

``` text
ARTIFACT_STORAGE_BACKEND=local
ARTIFACT_STORAGE_PATH=.artifacts
INGESTION_HTTP_TIMEOUT_SECONDS=30
INGESTION_USER_AGENT=nyc-housing-rag-mvp/0.1
HPD_VIOLATIONS_LIMIT=5000
```

Notes:

-   Local artifact storage is enough for Phase 3.
-   S3-compatible storage can be added later without changing ingestion
    interfaces.
-   Keep `.artifacts/` out of git.

## Step 1: Add Source Registry Models

Create models:

-   `Source`
-   `SourceVersion`
-   `IngestionRun`

Add an Alembic migration.

Acceptance criteria:

-   Tables are created successfully
-   `slug` is unique
-   duplicate source versions by hash are rejected or reused
-   ingestion run status can be recorded

## Step 2: Add Corpus Models

Create models:

-   `Document`
-   `Section`
-   `Chunk`
-   `Citation`
-   `HpdViolation`

Add indexes needed for traceability and future retrieval.

Acceptance criteria:

-   Every document references a source and source version
-   Every chunk references a source and source version
-   HPD violations reference source and source version
-   Chunk rows can be traced to raw artifact location through
    `source_versions`

## Step 3: Add Artifact Storage

Implement `app/ingestion/artifacts.py`.

Functions:

-   `ensure_artifact_storage()`
-   `write_artifact(source_slug, content_hash, content_bytes, extension)`
-   `read_artifact(artifact_uri)`
-   `artifact_exists(artifact_uri)`

For Phase 3, implement only local filesystem storage:

``` text
.artifacts/
└── sources/
    └── {source_slug}/
        └── {content_hash}.{extension}
```

Security considerations:

-   Do not write outside the configured artifact directory
-   Sanitize source slugs and file extensions
-   Do not serve raw artifacts publicly
-   Do not commit artifacts to git

Acceptance criteria:

-   Raw bytes are written with deterministic paths
-   Same artifact hash writes to same location
-   `.artifacts/` is gitignored

## Step 4: Seed Source Registry

Implement `app/ingestion/registry.py`.

Seed entries:

-   `nyc-housing-maintenance-code`
-   `ny-multiple-dwelling-law`
-   `ny-rpapl`
-   `hpd-guidance`
-   `hpd-violations`

CLI:

``` text
uv run python -m app.cli.ingest seed-sources
```

Each source entry should include public URL, publisher, access type,
license/terms status, and notes.

Acceptance criteria:

-   Running seed once creates sources
-   Running seed again updates changed metadata without duplicates
-   Sources with missing public URL or license status are rejected

## Step 5: Add Download Framework

Implement `app/ingestion/downloaders.py`.

Functions:

-   `download_url(url) -> DownloadedArtifact`
-   `hash_bytes(content_bytes) -> str`
-   `create_or_get_source_version(...)`

Requirements:

-   Use configured timeout
-   Send configured user agent
-   Follow reasonable redirects
-   Fail clearly for non-200 responses
-   Record content type and byte size
-   Store raw artifact before parsing

CLI:

``` text
uv run python -m app.cli.ingest download-source nyc-housing-maintenance-code
```

Acceptance criteria:

-   Downloaded artifact is hashed
-   Raw artifact is stored
-   Duplicate downloads with same hash reuse existing source version
-   Download errors create failed ingestion run records

## Step 6: Add Legal Text Parsing

Implement `app/ingestion/legal_text.py`.

Start with conservative parsing:

-   Convert HTML or plain text into normalized text
-   Split by legal section heading where possible
-   Preserve citation/title text
-   Store whole-source fallback chunk if structure cannot be confidently
    parsed

Functions:

-   `parse_legal_document(source, source_version, raw_text)`
-   `split_sections(raw_text)`
-   `create_chunks_from_sections(sections)`

For MVP, prefer correctness and traceability over aggressive parsing.

Acceptance criteria:

-   Legal source produces at least one document
-   Legal source produces sections when headings are detectable
-   Chunks preserve citation/title context
-   Re-running parse does not duplicate documents, sections, or chunks

## Step 7: Add Citation Normalization

Implement `app/ingestion/citations.py`.

Supported first citation families:

-   NYC Administrative Code / Housing Maintenance Code citations
-   Multiple Dwelling Law section citations
-   RPAPL section citations

Functions:

-   `normalize_citation(citation_text) -> str`
-   `detect_citation_type(citation_text) -> str`
-   `extract_citations(text) -> list[CitationCandidate]`

Acceptance criteria:

-   Equivalent formatting normalizes to one stable form
-   Citations are attached to sections/chunks when available
-   Parser does not invent citations

## Step 8: Add HPD Violations Loader

Implement `app/ingestion/hpd_violations.py`.

Source:

-   NYC Open Data HPD violations endpoint or export

Behavior:

-   Fetch a limited number of records for the MVP
-   Preserve raw JSON record
-   Map stable fields into columns
-   Upsert by external violation ID
-   Track created, updated, and skipped counts

CLI:

``` text
uv run python -m app.cli.ingest load-hpd-violations
```

Acceptance criteria:

-   Loader inserts HPD violation rows
-   Re-running loader does not duplicate rows
-   Changed rows update existing records
-   Raw record is preserved

## Step 9: Add Full Ingestion CLI

Implement `app/cli/ingest.py`.

Commands:

``` text
uv run python -m app.cli.ingest seed-sources
uv run python -m app.cli.ingest download-source SOURCE_SLUG
uv run python -m app.cli.ingest parse-source SOURCE_SLUG
uv run python -m app.cli.ingest ingest-source SOURCE_SLUG
uv run python -m app.cli.ingest load-hpd-violations
uv run python -m app.cli.ingest ingest-mvp
```

Command behavior:

-   Require admin auth only for future API endpoints; CLI can run locally
    with database access
-   Print concise success/failure summaries
-   Return non-zero exit code on failure
-   Record ingestion runs for all commands that mutate data

Acceptance criteria:

-   CLI can seed registry
-   CLI can ingest one legal source end to end
-   CLI can load HPD violations
-   CLI failures are visible in both terminal output and `ingestion_runs`

## Step 10: Add Tests

Minimum tests:

-   Source registry seed is idempotent
-   Source entries require public URL and license status
-   Byte hashing is deterministic
-   Artifact write path stays inside artifact root
-   Duplicate source versions are reused
-   Legal text parser creates document/section/chunk rows from sample input
-   Legal parser is idempotent
-   Citation normalization handles HMC, MDL, and RPAPL examples
-   HPD loader inserts sample records
-   HPD loader updates duplicate external IDs instead of duplicating
-   Failed ingestion run records error status

Use fixtures instead of live network calls in unit tests.

Live network smoke tests may be manual or marked separately so the default
test suite remains fast and deterministic.

## Step 11: Update Documentation

Update `README.md` with:

-   New environment variables
-   Artifact storage location
-   Source registry seed command
-   Single-source ingestion command
-   HPD violation load command
-   MVP ingestion command
-   Notes on free-source-only policy

Add a work-log entry after implementation.

## Security and Compliance Checklist

-   No paid data source is ingested
-   Every source has public URL and access notes
-   Raw artifacts are private and not served by the app
-   `.artifacts/` is ignored by git
-   Download timeouts are enforced
-   User agent is configured
-   HTTP errors are handled without leaking secrets
-   CLI does not print database credentials
-   Failed ingestion records do not store sensitive environment data
-   Parsers do not execute downloaded content
-   HTML parsing treats source content as untrusted input

## Manual Validation

After implementation:

1.  Apply migrations.

``` text
uv run alembic upgrade head
```

2.  Seed source registry.

``` text
uv run python -m app.cli.ingest seed-sources
```

3.  Ingest one legal source.

``` text
uv run python -m app.cli.ingest ingest-source nyc-housing-maintenance-code
```

4.  Load HPD violations.

``` text
uv run python -m app.cli.ingest load-hpd-violations
```

5.  Run MVP ingestion.

``` text
uv run python -m app.cli.ingest ingest-mvp
```

6.  Confirm database counts.

``` text
uv run python -m app.cli.ingest status
```

Expected:

-   Sources exist
-   Source versions exist
-   Documents and chunks exist for legal/guidance sources
-   HPD violations exist
-   Ingestion runs show succeeded status
-   Re-running commands does not duplicate rows

## Phase 3 Completion Criteria

Phase 3 is complete when:

-   Source registry tables exist
-   MVP sources can be seeded idempotently
-   Raw artifacts can be downloaded, hashed, and stored
-   Source versions are deduplicated by hash
-   Legal text can be parsed into documents, sections, chunks, and
    citations
-   HPD violations can be loaded and upserted
-   All ingested rows are traceable to source URL and source version
-   Ingestion runs record success and failure states
-   Tests pass
-   README is updated
-   Work log is updated

## Handoff to Phase 4

Phase 4 should build retrieval on top of Phase 3 data:

-   Exact citation lookup uses `citations` and `chunks`
-   Keyword search uses `chunks.text`
-   Vector search adds embeddings for `chunks`
-   Retrieval logs reference chunk IDs created in Phase 3
