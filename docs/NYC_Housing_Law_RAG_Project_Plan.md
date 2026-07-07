# NYC Housing Law RAG Project Plan

## 1. Project Goal

Build a searchable, citation-grounded knowledge system covering freely
accessible NYC housing law, regulations, agency guidance, public case
materials, and public housing/property datasets. It should support
citation-grounded answers, historical law lookup where public historical
versions are available, and property-specific queries.

## 2. Core Design Principle

Do **not** build one giant vector database. Build a **hybrid legal
retrieval system**.

Use only data sources that are freely accessible without paid data
subscriptions, private content licenses, scraping paywalled databases, or
relying on proprietary legal research platforms for source material. Paid
hosting, managed infrastructure, and API keys for compute, storage,
embedding, reranking, or LLM synthesis are allowed as implementation
choices. Every source must be tracked with its access method, license or
terms status, source URL, and redistribution constraints.

``` text
User Question
      │
Query Classifier
      │
 ┌────┴───────────────┐
 │                    │
Legal Retrieval   Property/Data Retrieval
 │                    │
Hybrid Search (BM25 + Vector + Metadata)
      │
   Reranker
      │
LLM Synthesis
      │
Citations + Confidence
```

## 3. Corpus Scope

### Primary Law

-   NYC Housing Maintenance Code from official NYC public law sources
-   NYC Administrative Code housing provisions from official NYC public
    sources
-   NYC Building Code housing provisions from official NYC public sources
-   Fire Code housing provisions from official NYC public sources
-   Health Code housing provisions from official NYC public sources
-   NYC Rent Stabilization Law from public NYC or state sources
-   Multiple Dwelling Law from official NY State public sources
-   Real Property Law from official NY State public sources
-   RPAPL from official NY State public sources
-   CPLR housing-related sections from official NY State public sources
-   Emergency Tenant Protection Act from official NY State public sources
-   Housing Stability and Tenant Protection Act public bill/session law
    materials

### Regulations

-   DHCR Rent Stabilization Code from public DHCR or NYCRR sources
-   HPD Rules from public NYC rules sources
-   DOB Rules from public NYC rules sources
-   Loft Board Rules from public NYC rules sources
-   OATH Rules from public NYC rules sources

### Agency Guidance

-   HPD tenant and owner guidance available on public HPD pages
-   Public HPD enforcement guidance, manuals, forms, and notices
-   DHCR fact sheets available on public DHCR pages
-   Public DHCR operational bulletins and policy statements
-   NY Attorney General tenant rights guides and public publications
-   Public NYC service pages, FAQs, and official forms

### Public Datasets

-   HPD violations from NYC Open Data
-   HPD complaints from NYC Open Data
-   HPD registrations from NYC Open Data
-   DOB violations from NYC Open Data
-   DOB complaints from NYC Open Data
-   PLUTO and MapPLUTO from public NYC Planning releases
-   ACRIS public property records and public bulk/download endpoints
-   Rent stabilized building lists from public NYC or NY State releases
-   Eviction and marshal data from public NYC Open Data or agency releases

### Case Law

-   NY Court of Appeals decisions available from public court sources
-   Appellate Division decisions available from public court sources
-   Appellate Term decisions available from public court sources where
    available
-   Housing Court decisions only when available from public court,
    government, or free public-domain repositories
-   DHCR administrative decisions only when published by DHCR or another
    free public source
-   OATH decisions published in public OATH repositories
-   Exclude Westlaw, Lexis, Bloomberg Law, Law360, proprietary headnotes,
    paid citators, commercial summaries, and any source whose terms do not
    permit the intended retrieval use

## 4. Product Architecture

``` text
User/API
   │
Query Classifier
   │
 ┌─────────────┬─────────────┬─────────────┐
 │             │             │
Legal      Property      Case Law
Search      Search        Search
 │             │             │
 └─────────────┴─────────────┘
         Reranker
            │
    Answer Generator
            │
 Citation + Confidence
```

## 5. Data Model

### Document Metadata

-   document_id
-   source_name
-   source_type
-   jurisdiction
-   publisher
-   effective_date
-   retrieved_at
-   source_url
-   version_hash
-   license_status
-   access_type
-   terms_url
-   redistribution_allowed
-   public_domain_or_open_license

### Chunk Metadata

-   chunk_id
-   document_id
-   citation
-   title
-   hierarchy
-   text
-   chunk_type
-   jurisdiction
-   topic_tags
-   effective_start
-   effective_end
-   superseded
-   cross_references

## 6. Retrieval Strategy

1.  Exact citation lookup
2.  Semantic legal retrieval
3.  BM25 keyword search
4.  Property-specific SQL/PostGIS lookup
5.  Source-access filter excluding non-free, paywalled, or restricted
    materials from retrieval and answer generation
6.  Reranking with local models or paid APIs, with API usage logged as an
    implementation dependency rather than a data source

## 7. Storage Stack

### MVP

-   PostgreSQL
-   pgvector
-   PostGIS
-   OpenSearch
-   MinIO or local S3-compatible storage
-   FastAPI
-   Local embedding model or paid embedding API

### Production

-   PostgreSQL + pgvector
-   OpenSearch
-   Neo4j Community Edition or Memgraph
-   Airflow/Dagster
-   Kafka/Redpanda
-   Redis
-   MinIO or other self-hosted object storage
-   OpenTelemetry plus hosted or self-hosted tracing/evaluation tools

## 8. Ingestion Pipeline

1.  Source registry
2.  Verify that each source is freely accessible and permitted for the
    intended use
3.  Download through official public exports, APIs, bulk files, or pages
4.  Hash raw documents and preserve source URLs
5.  Parse into canonical structure
6.  Normalize citations
7.  Structure-aware chunking
8.  Generate embeddings
9.  Index into search systems

## 9. Chunking

### Statutes

Chunk by legal hierarchy: - Section - Subsection - Paragraph -
Definition

### Guidance

Chunk by: - FAQ - Heading - Procedure - Tables

### Case Law

Chunk into: - Facts - Issue - Holding - Reasoning - Disposition

Do not ingest editorial enhancements from commercial databases, including
headnotes, key numbers, citator treatment, proprietary summaries, or
commercial annotations.

## 10. Knowledge Graph

### Nodes

-   Law
-   Regulation
-   Case
-   Agency document
-   Property
-   Violation
-   Court
-   Agency
-   Topic

### Relationships

-   cites
-   interprets
-   amends
-   enforces
-   defines
-   applies_to
-   administered_by

## 11. Query Classification

-   Legal explanation
-   Citation lookup
-   Property lookup
-   Violation lookup
-   Rent stabilization
-   Procedural guidance
-   Case law
-   Agency guidance
-   Historical law
-   Tenant mode
-   Owner mode

## 12. Answer Rules

Every answer should include: 1. Direct answer 2. Relevant law 3.
Practical implications 4. Exceptions 5. Citations 6. Legal disclaimer

## 13. Guardrails

-   Retrieval-only citations
-   Free-source-only retrieval
-   Source terms validation before ingestion
-   Jurisdiction validation
-   Version awareness
-   Hallucination prevention
-   Emergency escalation
-   Legal advice disclaimer

## 14. Evaluation

### Retrieval

-   Recall@5
-   Recall@10
-   MRR
-   Citation accuracy
-   Jurisdiction accuracy
-   Free-source compliance

### Answer Quality

-   Legal correctness
-   Citation correctness
-   Completeness
-   Clarity
-   Practical usefulness

### Red Team

-   Fake citations
-   Outdated laws
-   Conflicting authority
-   Ambiguous questions
-   Accidental use of paid-only or restricted sources

## 15. Versioning

Each chunk should include: - effective_start - effective_end -
source_version - retrieved_at - current flag

Historical coverage should be explicit. If only the current public version
is available, mark historical lookup as unavailable for that source instead
of inferring prior law.

## 16. Roadmap

### Phase 0

-   Housing Maintenance Code
-   Multiple Dwelling Law
-   Public-source registry
-   Basic search over verified free sources

### Phase 1

-   Freely accessible legal corpus
-   Hybrid retrieval
-   API
-   Source access and license audit

### Phase 2

-   Property datasets
-   Address lookup
-   Violations integration

### Phase 3

-   Freely accessible case law and administrative decisions
-   Citation graph
-   Precedent retrieval with public-source coverage labels

### Phase 4

-   Monitoring
-   Scheduled updates
-   Human review
-   Admin dashboard
-   Source availability and terms-change monitoring

## 17. Technology Stack

-   Python
-   FastAPI
-   PostgreSQL
-   pgvector
-   OpenSearch
-   LangGraph
-   Next.js
-   Redis
-   MinIO
-   OpenTelemetry
-   Self-hosted evaluation/tracing tools

Paid hosting, managed databases, object storage, model APIs, and
observability SaaS are acceptable. Paid data access, paid legal research
content, proprietary case summaries, and paywalled source material are not
acceptable corpus sources.

## 18. Database Tables

-   sources
-   source_access_reviews
-   source_versions
-   documents
-   sections
-   chunks
-   embeddings
-   citations
-   cross_references
-   topics
-   cases
-   properties
-   violations
-   complaints
-   retrieval_logs
-   evaluation_results

## 19. API

-   POST /search
-   POST /answer
-   GET /citation/{id}

## 20. Risks

-   Source licensing and terms restrictions
-   Incomplete public case law coverage
-   Hallucinations
-   Stale law
-   Poor chunking
-   Mixing legal authority with guidance
-   Treating unavailable paid-only materials as if they were searched

## 21. MVP

-   Housing Maintenance Code
-   Multiple Dwelling Law
-   RPAPL
-   HPD guidance
-   HPD violations
-   Hybrid search
-   Citation-grounded answers
-   Only verified free public sources
-   Clear coverage disclaimers for omitted paid-only or unavailable
    materials

## 22. Long-Term Vision

Create a legal retrieval infrastructure with: - Exact citations - Source
hierarchy - Historical versioning where public versions are available -
Hybrid retrieval - Knowledge graph - Property intelligence - Automated
updates - Evaluation framework - Human review workflows - Clear coverage
boundaries for sources that are not freely accessible
