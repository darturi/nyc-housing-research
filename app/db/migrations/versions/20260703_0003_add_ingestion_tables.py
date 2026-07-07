"""Add ingestion tables.

Revision ID: 20260703_0003
Revises: 20260701_0002
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260703_0003"
down_revision: str | Sequence[str] | None = "20260701_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=False),
        sa.Column("jurisdiction", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("access_type", sa.String(length=80), nullable=False),
        sa.Column("license_status", sa.String(length=120), nullable=False),
        sa.Column("terms_url", sa.Text(), nullable=True),
        sa.Column("redistribution_allowed", sa.Boolean(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_sources_slug"), "sources", ["slug"], unique=True)

    op.create_table(
        "source_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_uri", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("effective_start", sa.Date(), nullable=True),
        sa.Column("effective_end", sa.Date(), nullable=True),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id", "content_hash", name="uq_source_version_hash"),
    )
    op.create_index(
        op.f("ix_source_versions_source_id"),
        "source_versions",
        ["source_id"],
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("source_version_id", sa.String(length=36), nullable=True),
        sa.Column("run_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("records_created", sa.Integer(), nullable=False),
        sa.Column("records_updated", sa.Integer(), nullable=False),
        sa.Column("records_skipped", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_ingestion_runs_source_id"),
        "ingestion_runs",
        ["source_id"],
    )
    op.create_index(
        op.f("ix_ingestion_runs_source_version_id"),
        "ingestion_runs",
        ["source_version_id"],
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("document_key", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("document_type", sa.String(length=80), nullable=False),
        sa.Column("jurisdiction", sa.String(length=50), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_version_id",
            "document_key",
            name="uq_document_key",
        ),
    )
    op.create_index(op.f("ix_documents_source_id"), "documents", ["source_id"])
    op.create_index(
        op.f("ix_documents_source_version_id"),
        "documents",
        ["source_version_id"],
    )

    op.create_table(
        "sections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("parent_section_id", sa.String(length=36), nullable=True),
        sa.Column("section_key", sa.String(length=255), nullable=False),
        sa.Column("citation", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("hierarchy_path", sa.String(length=500), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_section_id"],
            ["sections.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "section_key", name="uq_section_key"),
    )
    op.create_index(op.f("ix_sections_citation"), "sections", ["citation"])
    op.create_index(op.f("ix_sections_document_id"), "sections", ["document_id"])
    op.create_index(
        op.f("ix_sections_parent_section_id"),
        "sections",
        ["parent_section_id"],
    )

    op.create_table(
        "chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=36), nullable=True),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_key", sa.String(length=255), nullable=False),
        sa.Column("chunk_type", sa.String(length=80), nullable=False),
        sa.Column("citation", sa.String(length=255), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_version_id", "chunk_key", name="uq_chunk_key"),
    )
    op.create_index(op.f("ix_chunks_citation"), "chunks", ["citation"])
    op.create_index(op.f("ix_chunks_document_id"), "chunks", ["document_id"])
    op.create_index(op.f("ix_chunks_section_id"), "chunks", ["section_id"])
    op.create_index(op.f("ix_chunks_source_id"), "chunks", ["source_id"])
    op.create_index(
        op.f("ix_chunks_source_version_id"),
        "chunks",
        ["source_version_id"],
    )
    op.create_index(op.f("ix_chunks_text_hash"), "chunks", ["text_hash"])

    op.create_table(
        "citations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("section_id", sa.String(length=36), nullable=True),
        sa.Column("chunk_id", sa.String(length=36), nullable=True),
        sa.Column("citation_text", sa.String(length=255), nullable=False),
        sa.Column("normalized_citation", sa.String(length=255), nullable=False),
        sa.Column("citation_type", sa.String(length=80), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_citations_chunk_id"), "citations", ["chunk_id"])
    op.create_index(op.f("ix_citations_document_id"), "citations", ["document_id"])
    op.create_index(
        op.f("ix_citations_normalized_citation"),
        "citations",
        ["normalized_citation"],
    )
    op.create_index(op.f("ix_citations_section_id"), "citations", ["section_id"])
    op.create_index(op.f("ix_citations_source_id"), "citations", ["source_id"])

    op.create_table(
        "hpd_violations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_version_id", sa.String(length=36), nullable=False),
        sa.Column("external_id", sa.String(length=120), nullable=False),
        sa.Column("building_id", sa.String(length=120), nullable=True),
        sa.Column("registration_id", sa.String(length=120), nullable=True),
        sa.Column("boro", sa.String(length=80), nullable=True),
        sa.Column("house_number", sa.String(length=80), nullable=True),
        sa.Column("street_name", sa.String(length=255), nullable=True),
        sa.Column("zip_code", sa.String(length=20), nullable=True),
        sa.Column("apartment", sa.String(length=80), nullable=True),
        sa.Column("class", sa.String(length=20), nullable=True),
        sa.Column("inspection_date", sa.Date(), nullable=True),
        sa.Column("approved_date", sa.Date(), nullable=True),
        sa.Column("original_certify_by_date", sa.Date(), nullable=True),
        sa.Column("original_correct_by_date", sa.Date(), nullable=True),
        sa.Column("new_certify_by_date", sa.Date(), nullable=True),
        sa.Column("new_correct_by_date", sa.Date(), nullable=True),
        sa.Column("certified_date", sa.Date(), nullable=True),
        sa.Column("order_number", sa.String(length=120), nullable=True),
        sa.Column("nov_id", sa.String(length=120), nullable=True),
        sa.Column("nov_description", sa.Text(), nullable=True),
        sa.Column("current_status", sa.String(length=120), nullable=True),
        sa.Column("current_status_date", sa.Date(), nullable=True),
        sa.Column("raw_record", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_hpd_violations_building_id"),
        "hpd_violations",
        ["building_id"],
    )
    op.create_index(
        op.f("ix_hpd_violations_current_status"),
        "hpd_violations",
        ["current_status"],
    )
    op.create_index(
        op.f("ix_hpd_violations_external_id"),
        "hpd_violations",
        ["external_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_hpd_violations_house_number"),
        "hpd_violations",
        ["house_number"],
    )
    op.create_index(
        op.f("ix_hpd_violations_registration_id"),
        "hpd_violations",
        ["registration_id"],
    )
    op.create_index(
        op.f("ix_hpd_violations_source_id"),
        "hpd_violations",
        ["source_id"],
    )
    op.create_index(
        op.f("ix_hpd_violations_source_version_id"),
        "hpd_violations",
        ["source_version_id"],
    )
    op.create_index(
        op.f("ix_hpd_violations_street_name"),
        "hpd_violations",
        ["street_name"],
    )
    op.create_index(
        op.f("ix_hpd_violations_zip_code"),
        "hpd_violations",
        ["zip_code"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_hpd_violations_zip_code"), table_name="hpd_violations")
    op.drop_index(op.f("ix_hpd_violations_street_name"), table_name="hpd_violations")
    op.drop_index(
        op.f("ix_hpd_violations_source_version_id"),
        table_name="hpd_violations",
    )
    op.drop_index(op.f("ix_hpd_violations_source_id"), table_name="hpd_violations")
    op.drop_index(
        op.f("ix_hpd_violations_registration_id"),
        table_name="hpd_violations",
    )
    op.drop_index(op.f("ix_hpd_violations_house_number"), table_name="hpd_violations")
    op.drop_index(op.f("ix_hpd_violations_external_id"), table_name="hpd_violations")
    op.drop_index(
        op.f("ix_hpd_violations_current_status"),
        table_name="hpd_violations",
    )
    op.drop_index(op.f("ix_hpd_violations_building_id"), table_name="hpd_violations")
    op.drop_table("hpd_violations")

    op.drop_index(op.f("ix_citations_source_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_section_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_normalized_citation"), table_name="citations")
    op.drop_index(op.f("ix_citations_document_id"), table_name="citations")
    op.drop_index(op.f("ix_citations_chunk_id"), table_name="citations")
    op.drop_table("citations")

    op.drop_index(op.f("ix_chunks_text_hash"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_source_version_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_source_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_section_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_document_id"), table_name="chunks")
    op.drop_index(op.f("ix_chunks_citation"), table_name="chunks")
    op.drop_table("chunks")

    op.drop_index(op.f("ix_sections_parent_section_id"), table_name="sections")
    op.drop_index(op.f("ix_sections_document_id"), table_name="sections")
    op.drop_index(op.f("ix_sections_citation"), table_name="sections")
    op.drop_table("sections")

    op.drop_index(op.f("ix_documents_source_version_id"), table_name="documents")
    op.drop_index(op.f("ix_documents_source_id"), table_name="documents")
    op.drop_table("documents")

    op.drop_index(
        op.f("ix_ingestion_runs_source_version_id"),
        table_name="ingestion_runs",
    )
    op.drop_index(op.f("ix_ingestion_runs_source_id"), table_name="ingestion_runs")
    op.drop_table("ingestion_runs")

    op.drop_index(op.f("ix_source_versions_source_id"), table_name="source_versions")
    op.drop_table("source_versions")

    op.drop_index(op.f("ix_sources_slug"), table_name="sources")
    op.drop_table("sources")
