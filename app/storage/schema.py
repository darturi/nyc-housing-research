from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)

CORPUS_SCHEMA_VERSION = 3
STATE_SCHEMA_VERSION = 2

corpus_metadata = MetaData()
state_metadata = MetaData()

corpus_schema_metadata = Table(
    "schema_metadata",
    corpus_metadata,
    Column("key", String(80), primary_key=True),
    Column("value", String(255), nullable=False),
)

source_modules = Table(
    "source_modules",
    corpus_metadata,
    Column("id", String(36), primary_key=True),
    Column("slug", String(160), nullable=False, unique=True, index=True),
    Column("name", String(255), nullable=False),
    Column("source_type", String(40), nullable=False),
    Column("publisher", String(255), nullable=False),
    Column("jurisdiction", String(120), nullable=False),
    Column("source_url", Text),
    Column("scope_json", Text, nullable=False),
    Column("manifest_json", Text, nullable=False),
    Column("origin", String(40), nullable=False, default="core", server_default="core"),
    Column(
        "acquisition_kind",
        String(40),
        nullable=False,
        default="managed_download",
        server_default="managed_download",
    ),
    Column(
        "model_use_allowed",
        Boolean,
        nullable=False,
        default=True,
        server_default="1",
    ),
    Column("enabled", Boolean, nullable=False, default=True),
)

source_versions = Table(
    "source_versions",
    corpus_metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "source_module_id",
        String(36),
        ForeignKey("source_modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("content_hash", String(64), nullable=False, index=True),
    Column("parser_version", String(80), nullable=False),
    Column("artifact_uri", Text, nullable=False),
    Column("retrieved_at", DateTime(timezone=True), nullable=False),
    Column("last_checked_at", DateTime(timezone=True), nullable=False),
    Column("effective_from", DateTime(timezone=True)),
    Column("effective_to", DateTime(timezone=True)),
    Column("validation_state", String(40), nullable=False),
    Column("validation_json", Text, nullable=False),
    Column("provenance_json", Text, nullable=False, default="{}", server_default="{}"),
    UniqueConstraint(
        "source_module_id",
        "content_hash",
        "parser_version",
        name="uq_local_source_version_content_parser",
    ),
)

documents = Table(
    "documents",
    corpus_metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "source_version_id",
        String(36),
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("stable_id", String(255), nullable=False),
    Column("title", Text, nullable=False),
    Column("source_url", Text),
    UniqueConstraint(
        "source_version_id", "stable_id", name="uq_local_document_version_stable"
    ),
)

chunks = Table(
    "chunks",
    corpus_metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "document_id",
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "source_module_id",
        String(36),
        ForeignKey("source_modules.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "source_version_id",
        String(36),
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("stable_id", String(255), nullable=False),
    Column("citation", String(255)),
    Column("title", Text),
    Column("text", Text, nullable=False),
    Column("text_hash", String(64), nullable=False, index=True),
    Column("locator_json", Text, nullable=False, default="{}", server_default="{}"),
    UniqueConstraint(
        "source_version_id", "stable_id", name="uq_local_chunk_version_stable"
    ),
)

citations = Table(
    "citations",
    corpus_metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "chunk_id",
        String(64),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("normalized_citation", String(255), nullable=False, index=True),
    Column("display_citation", String(255), nullable=False),
    UniqueConstraint("chunk_id", "normalized_citation", name="uq_local_chunk_citation"),
)

embedding_profiles = Table(
    "embedding_profiles",
    corpus_metadata,
    Column("id", String(160), primary_key=True),
    Column("provider", String(80), nullable=False),
    Column("model", String(160), nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("preprocessing_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

embeddings = Table(
    "embeddings",
    corpus_metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "chunk_id",
        String(64),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column(
        "profile_id",
        String(160),
        ForeignKey("embedding_profiles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("dimension", Integer, nullable=False),
    Column("dtype", String(20), nullable=False),
    Column("normalized", Boolean, nullable=False),
    Column("vector_bytes", LargeBinary, nullable=False),
    Column("checksum", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("chunk_id", "profile_id", name="uq_local_chunk_profile"),
)

generations = Table(
    "generations",
    corpus_metadata,
    Column("id", String(36), primary_key=True),
    Column("status", String(40), nullable=False, index=True),
    Column("profile_id", String(160)),
    Column("readiness", String(40), nullable=False),
    Column("is_partial", Boolean, nullable=False),
    Column("validation_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("activated_at", DateTime(timezone=True)),
)

generation_sources = Table(
    "generation_sources",
    corpus_metadata,
    Column(
        "generation_id",
        String(36),
        ForeignKey("generations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "source_version_id",
        String(36),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)

generation_chunks = Table(
    "generation_chunks",
    corpus_metadata,
    Column(
        "generation_id",
        String(36),
        ForeignKey("generations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "chunk_id",
        String(64),
        ForeignKey("chunks.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column("text_ready", Boolean, nullable=False),
    Column("embedding_ready", Boolean, nullable=False),
)

corpus_state = Table(
    "corpus_state",
    corpus_metadata,
    Column("id", Integer, primary_key=True),
    Column("active_generation_id", String(36)),
)

corpus_operations = Table(
    "corpus_operations",
    corpus_metadata,
    Column("operation_id", String(64), primary_key=True),
    Column("operation_type", String(80), nullable=False),
    Column("source_module_id", String(36)),
    Column("source_version_id", String(36)),
    Column("generation_id", String(36), nullable=False),
    Column("result_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

state_schema_metadata = Table(
    "schema_metadata",
    state_metadata,
    Column("key", String(80), primary_key=True),
    Column("value", String(255), nullable=False),
)

application_settings = Table(
    "application_settings",
    state_metadata,
    Column("key", String(160), primary_key=True),
    Column("value_json", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

jobs = Table(
    "jobs",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("job_type", String(80), nullable=False, index=True),
    Column("target_id", String(255), nullable=False, index=True),
    Column("state", String(40), nullable=False, index=True),
    Column("stage", String(80), nullable=False),
    Column("progress_current", Integer, nullable=False),
    Column("progress_total", Integer),
    Column("retryable", Boolean, nullable=False),
    Column("resume_json", Text, nullable=False),
    Column("error_code", String(80)),
    Column("error_message", Text),
    Column("lease_owner", String(160)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("cancel_requested_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

usage_events = Table(
    "usage_events",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("operation_id", String(36), nullable=False, index=True),
    Column("attempt_id", String(36), nullable=False, index=True),
    Column("event_type", String(40), nullable=False),
    Column("provider", String(80), nullable=False),
    Column("profile_id", String(160), nullable=False),
    Column("amount_usd", Numeric(18, 8), nullable=False),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("price_snapshot_json", Text, nullable=False),
    Column("status", String(40), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

paid_call_leases = Table(
    "paid_call_leases",
    state_metadata,
    Column("attempt_id", String(36), primary_key=True),
    Column("operation_id", String(36), nullable=False, index=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

maintenance_state = Table(
    "maintenance_state",
    state_metadata,
    Column("id", Integer, primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("operation", String(80)),
    Column("started_at", DateTime(timezone=True)),
)

property_cache = Table(
    "property_cache",
    state_metadata,
    Column("cache_key", String(64), primary_key=True),
    Column("request_json", Text, nullable=False),
    Column("connector_version", String(80), nullable=False),
    Column("response_artifact", Text, nullable=False),
    Column("status", String(40), nullable=False),
    Column("is_complete", Boolean, nullable=False),
    Column("fetched_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("stale_until", DateTime(timezone=True), nullable=False),
    Column("last_accessed_at", DateTime(timezone=True), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("pinned", Boolean, nullable=False),
)

local_sessions = Table(
    "local_sessions",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("csrf_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
)

local_installation = Table(
    "local_installation",
    state_metadata,
    Column("id", Integer, primary_key=True),
    Column("installation_id", String(36), nullable=False, unique=True),
    Column("secret_hash", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

launcher_tokens = Table(
    "launcher_tokens",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True)),
)

# User-owned research records live in the state database.  They deliberately do
# not use foreign keys into the corpus database: retained evidence must remain
# readable after a corpus version is removed or a generation changes.
matters = Table(
    "matters",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(255), nullable=False),
    Column("description", Text, nullable=False, default="", server_default=""),
    Column("tags_json", Text, nullable=False, default="[]", server_default="[]"),
    Column("archived", Boolean, nullable=False, default=False, server_default="0"),
    Column("revision", Integer, nullable=False, default=1, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

saved_items = Table(
    "saved_items",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(40), nullable=False, index=True),
    Column("payload_version", Integer, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False, index=True),
    Column("source_identity", String(255), nullable=False, index=True),
    Column("original_operation_id", String(64), index=True),
    Column("parent_item_id", String(36), ForeignKey("saved_items.id")),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

matter_items = Table(
    "matter_items",
    state_metadata,
    Column(
        "matter_id",
        String(36),
        ForeignKey("matters.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "item_id",
        String(36),
        ForeignKey("saved_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("display_order", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

matter_notes = Table(
    "matter_notes",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("matter_id", String(36), ForeignKey("matters.id", ondelete="CASCADE")),
    Column("item_id", String(36), ForeignKey("saved_items.id", ondelete="CASCADE")),
    Column("body", Text, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

save_receipts = Table(
    "save_receipts",
    state_metadata,
    Column("idempotency_key", String(160), primary_key=True),
    Column("source_identity", String(255), nullable=False),
    Column("item_id", String(36), ForeignKey("saved_items.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

source_comparisons = Table(
    "source_comparisons",
    state_metadata,
    Column("id", String(64), primary_key=True),
    Column("module_slug", String(160), nullable=False, index=True),
    Column("baseline_version_id", String(36), nullable=False),
    Column("target_version_id", String(36), nullable=False),
    Column("normalization_version", String(40), nullable=False),
    Column("matcher_version", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("counts_json", Text, nullable=False),
    Column("warning", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "module_slug",
        "baseline_version_id",
        "target_version_id",
        "normalization_version",
        "matcher_version",
        name="uq_source_comparison_inputs",
    ),
)

comparison_changes = Table(
    "comparison_changes",
    state_metadata,
    Column("id", String(64), primary_key=True),
    Column(
        "comparison_id",
        String(64),
        ForeignKey("source_comparisons.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("canonical_key", String(512), nullable=False),
    Column("classification", String(40), nullable=False, index=True),
    Column("old_json", Text),
    Column("new_json", Text),
    Column("diff_text", Text),
    Column("certainty", String(40), nullable=False),
    UniqueConstraint("comparison_id", "canonical_key", name="uq_comparison_change_key"),
)

comparison_reviews = Table(
    "comparison_reviews",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("item_id", String(36), ForeignKey("saved_items.id"), nullable=False),
    Column(
        "comparison_id",
        String(64),
        ForeignKey("source_comparisons.id"),
        nullable=False,
    ),
    Column("note", Text, nullable=False, default="", server_default=""),
    Column("reviewed_at", DateTime(timezone=True), nullable=False),
)

property_identities = Table(
    "property_identities",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("entity_kind", String(40), nullable=False),
    Column("display_address", Text, nullable=False),
    Column("normalized_address", String(512), nullable=False, index=True),
    Column("identifiers_json", Text, nullable=False),
    Column("provenance_json", Text, nullable=False),
    Column("revision", Integer, nullable=False, default=1, server_default="1"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

dossier_observations = Table(
    "dossier_observations",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "property_identity_id",
        String(36),
        ForeignKey("property_identities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
    Column("requested_panels_json", Text, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Column("observed_at", DateTime(timezone=True), nullable=False),
)

extraction_runs = Table(
    "extraction_runs",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("resource_id", String(36), nullable=False, index=True),
    Column("source_version_id", String(36), nullable=False),
    Column("operation", String(40), nullable=False),
    Column("status", String(40), nullable=False),
    Column("report_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

help_feedback = Table(
    "help_feedback",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("rule_ids_json", Text, nullable=False),
    Column("topic", String(80), nullable=False),
    Column("helpful", Boolean, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

extension_installations = Table(
    "extension_installations",
    state_metadata,
    Column("id", String(36), primary_key=True),
    Column("extension_id", String(160), nullable=False, index=True),
    Column("kind", String(80), nullable=False),
    Column("version", String(80), nullable=False),
    Column("state", String(40), nullable=False),
    Column("manifest_json", Text, nullable=False),
    Column("artifact_uri", Text),
    Column("size_bytes", Integer, nullable=False, default=0, server_default="0"),
    Column("active", Boolean, nullable=False, default=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
