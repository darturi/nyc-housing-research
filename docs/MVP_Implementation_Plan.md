# NYC Housing Law RAG MVP Implementation Plan

## 1. MVP Objective

Build a password-protected web/API application that answers NYC housing
law and property-related questions using only freely accessible public data
sources. The first version should prove the retrieval and citation workflow
end to end without paid legal data, a graph database, OpenSearch, Kafka, or
complex orchestration.

## 2. MVP Scope

### Included Sources

-   NYC Housing Maintenance Code from official public NYC sources
-   Multiple Dwelling Law from official public NY State sources
-   RPAPL from official public NY State sources
-   HPD public tenant/owner guidance
-   HPD violations from NYC Open Data

### Included Capabilities

-   Password-protected access for a small private user group
-   Citation-grounded question answering
-   Exact citation lookup
-   Keyword search
-   Vector search with `pgvector`
-   Address or BBL-based HPD violation lookup where data supports it
-   Basic source freshness and ingestion logs
-   Admin-only ingestion trigger or command-line ingestion job

### Explicitly Deferred

-   Public user registration
-   Paid legal databases
-   Case law retrieval
-   OpenSearch
-   Knowledge graph
-   Automated citator treatment
-   Multi-tenant billing
-   Real-time data streaming
-   Admin dashboard beyond minimal logs/status

## 3. Minimal Paid Resources

-   App hosting for the FastAPI backend and frontend
-   Managed PostgreSQL with `pgvector`; `PostGIS` if property lookup is in
    the first deploy
-   Object storage for raw downloads and parsed artifacts
-   Embedding API or hosted embedding model
-   LLM API for answer synthesis
-   Optional transactional email only if password reset or invites are
    implemented

No paid resource should provide corpus data. Paid services may provide
compute, storage, models, monitoring, and deployment infrastructure only.

## 4. Architecture

``` text
Browser
  |
Password-Protected Web App
  |
FastAPI
  |
Auth / Rate Limit / Request Validation
  |
Query Classifier
  |
PostgreSQL
  |- documents / chunks / citations
  |- pgvector embeddings
  |- HPD violations
  |- retrieval logs
  |
LLM + Embedding APIs
  |
Answer with citations and disclaimer
```

## 5. Suggested First Stack

-   Backend: FastAPI
-   Frontend: simple server-rendered pages or a minimal Next.js app
-   Database: managed PostgreSQL with `pgvector`
-   Property extension: `PostGIS`, if available on the selected database
-   Object storage: S3-compatible bucket
-   Jobs: CLI command or scheduled host job
-   Auth: application-managed username/password login with secure cookies
-   Rate limiting: database-backed or Redis-backed limiter
-   Secrets: hosting provider secret manager or encrypted environment
    variables

For the first version, avoid adding Redis unless the hosting platform makes
it cheap and simple. Database-backed rate limiting is enough for a small
private MVP.

## 6. Data Model

### Core Tables

-   `users`
-   `sessions`
-   `sources`
-   `source_versions`
-   `documents`
-   `chunks`
-   `chunk_embeddings`
-   `citations`
-   `hpd_violations`
-   `retrieval_logs`
-   `answer_logs`
-   `rate_limit_events`

### Required Source Fields

-   `source_name`
-   `source_url`
-   `publisher`
-   `access_type`
-   `license_status`
-   `terms_url`
-   `retrieved_at`
-   `version_hash`
-   `redistribution_allowed`
-   `notes`

## 7. Implementation Phases

### Phase 1: Project Skeleton

-   Create FastAPI app structure
-   Add database migrations
-   Add configuration loading
-   Add health check endpoint
-   Add structured logging
-   Add deployment configuration

Acceptance criteria:

-   App boots locally and in hosted environment
-   Database migrations run from a clean database
-   Health check confirms database connectivity

### Phase 2: Password-Protected Access

-   Create `users` and `sessions` tables
-   Store passwords using Argon2id or bcrypt
-   Add login/logout endpoints
-   Use secure, HTTP-only, same-site cookies
-   Require authentication for all search and answer endpoints
-   Create an admin-only user creation command
-   Disable public signup for the MVP

Acceptance criteria:

-   Anonymous users cannot access the app or API
-   A seeded admin can create private users
-   Session cookies are not readable by JavaScript
-   Sessions expire after a fixed period

### Phase 3: Source Registry and Ingestion

-   Add source registry entries for the MVP corpus
-   Download public source documents and datasets
-   Store raw artifacts in object storage
-   Hash source artifacts for version tracking
-   Parse legal text into sections and chunks
-   Normalize citations
-   Load HPD violations into relational tables
-   Record ingestion status and errors

Acceptance criteria:

-   Every ingested source has a public URL and license/terms status
-   Re-running ingestion is idempotent
-   Raw files and parsed chunks can be traced back to source URLs

### Phase 4: Retrieval

-   Implement exact citation lookup
-   Implement PostgreSQL full-text keyword search
-   Generate embeddings for chunks
-   Implement vector search with `pgvector`
-   Merge exact, keyword, and vector results
-   Add metadata filters for source type and jurisdiction
-   Log retrieved chunk IDs for every answer request

Acceptance criteria:

-   Search returns citations and source links
-   Exact citation requests prefer exact matches
-   Answer generation cannot cite chunks that were not retrieved

### Phase 5: Answer Generation

-   Build prompt template with strict citation rules
-   Include direct answer, relevant law, practical implications,
    exceptions, citations, and disclaimer
-   Add refusal behavior when the corpus does not support an answer
-   Add source coverage disclosure for omitted paid or unavailable sources
-   Store answer logs with retrieved chunk IDs, model name, and token usage

Acceptance criteria:

-   Answers include citations from retrieved chunks only
-   Unsupported questions receive a clear limitation message
-   Legal disclaimer is always present

### Phase 6: Rate Limiting and Abuse Controls

-   Apply per-user request limits
-   Apply per-IP unauthenticated login attempt limits
-   Apply daily token or cost budgets per user
-   Add request body size limits
-   Add timeout limits for search and answer calls
-   Add lockout or cooldown for repeated failed logins

Initial limits:

-   Login attempts: 5 failed attempts per IP per 15 minutes
-   Answer requests: 30 per user per hour
-   Search requests: 120 per user per hour
-   Daily LLM budget: configurable per user
-   Max question length: 4,000 characters
-   Max retrieved chunks sent to LLM: 8 to 12

Acceptance criteria:

-   Excessive requests return `429 Too Many Requests`
-   Failed login bursts are slowed or blocked
-   One user cannot exhaust the whole API budget

### Phase 7: Deployment and Operations

-   Configure HTTPS-only access
-   Set production secrets outside source control
-   Enable database backups
-   Enable object storage lifecycle policy
-   Add basic uptime monitoring
-   Add error logging
-   Add manual ingestion runbook
-   Add rollback instructions

Acceptance criteria:

-   Production app is reachable only over HTTPS
-   Secrets are not committed to the repository
-   Backups are enabled and restore has been tested once

## 8. Security Requirements

### Authentication

-   No public signup in the MVP
-   Admin-created users only
-   Argon2id preferred for password hashing; bcrypt acceptable
-   Password minimum length of at least 12 characters
-   Session cookies must be `HttpOnly`, `Secure`, and `SameSite=Lax` or
    stricter
-   Session expiration should be enforced server-side

### Authorization

-   Require authentication for every non-health endpoint
-   Separate normal users from admin users
-   Restrict ingestion triggers and user management to admins
-   Never expose raw environment variables, API keys, or object storage
    credentials through the UI or logs

### Input and Prompt Safety

-   Validate request size and schema
-   Treat all user questions as untrusted input
-   Keep system prompts server-side
-   Prevent user input from overriding citation rules
-   Never allow the model to cite sources outside retrieved chunks
-   Store model outputs with source chunk IDs for auditability

### Data Security

-   Use TLS for app and database connections
-   Encrypt managed database and object storage at rest
-   Keep raw downloaded source files separate from generated artifacts
-   Avoid storing unnecessary personal data
-   Redact user questions from logs if they may include sensitive housing
    facts

### Secret Management

-   Store API keys in provider secret management or environment variables
-   Rotate API keys before public testing
-   Use separate development and production keys
-   Do not expose model provider keys to the browser

## 9. Rate Limiting Design

Use layered limits:

-   IP-based limits for unauthenticated endpoints
-   User-based limits for authenticated search and answer endpoints
-   Cost-based limits for LLM and embedding calls
-   Admin bypass only for trusted ingestion and debugging operations

For the MVP, rate limit state can live in PostgreSQL. If request volume
grows or multiple app instances are deployed, move rate limit state to
Redis.

## 10. Password-Protected Access Design

The simplest acceptable MVP access model is private accounts:

-   Admin creates user accounts manually
-   User logs in with email and password
-   Server creates a session record
-   Browser receives an HTTP-only secure session cookie
-   API checks the session on every request
-   Logout deletes the session
-   Expired sessions are rejected

Avoid shared passwords for the deployed MVP because they make auditing,
revocation, and rate limiting much weaker.

## 11. MVP Test Plan

-   Unit test citation normalization
-   Unit test chunking behavior
-   Unit test auth and session expiration
-   Unit test rate limit decisions
-   Integration test ingestion for each MVP source
-   Integration test search and answer flow
-   Security test unauthenticated access
-   Security test repeated failed login attempts
-   Regression test answers cannot cite non-retrieved chunks

## 12. Launch Checklist

-   All source records include public URLs and access notes
-   Authentication is required outside `/health`
-   Password hashing is enabled
-   Session cookies are secure in production
-   Rate limits are active
-   LLM/API keys are server-side only
-   Database backups are enabled
-   Object storage bucket is private
-   Error logs do not expose secrets
-   Legal disclaimer appears on every answer
-   Coverage disclaimer explains that paid legal databases are not searched
