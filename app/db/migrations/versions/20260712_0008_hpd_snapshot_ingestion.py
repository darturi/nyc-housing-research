"""Add HPD snapshot checkpoints and normalized address lookup fields.

Revision ID: 20260712_0008
Revises: 20260705_0007
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0008"
down_revision: str | Sequence[str] | None = "20260705_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hpd_violations", sa.Column("normalized_house_number", sa.String(80))
    )
    op.add_column(
        "hpd_violations", sa.Column("normalized_street_name", sa.String(255))
    )
    op.add_column(
        "hpd_violations", sa.Column("normalized_full_address", sa.String(400))
    )
    op.create_index(
        op.f("ix_hpd_violations_normalized_house_number"),
        "hpd_violations",
        ["normalized_house_number"],
    )
    op.create_index(
        op.f("ix_hpd_violations_normalized_street_name"),
        "hpd_violations",
        ["normalized_street_name"],
    )
    op.create_index(
        op.f("ix_hpd_violations_normalized_full_address"),
        "hpd_violations",
        ["normalized_full_address"],
    )
    op.create_table(
        "hpd_ingestion_checkpoints",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("run_mode", sa.String(30), nullable=False),
        sa.Column("last_page_key", sa.JSON(), nullable=True),
        sa.Column("status_date_watermark", sa.Date(), nullable=True),
        sa.Column("artifact_manifest_uri", sa.Text(), nullable=True),
        sa.Column("source_version_id", sa.String(36), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["source_versions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_index(
        op.f("ix_hpd_ingestion_checkpoints_source_id"),
        "hpd_ingestion_checkpoints",
        ["source_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_hpd_ingestion_checkpoints_source_id"),
        table_name="hpd_ingestion_checkpoints",
    )
    op.drop_table("hpd_ingestion_checkpoints")
    op.drop_index(
        op.f("ix_hpd_violations_normalized_full_address"),
        table_name="hpd_violations",
    )
    op.drop_index(
        op.f("ix_hpd_violations_normalized_street_name"),
        table_name="hpd_violations",
    )
    op.drop_index(
        op.f("ix_hpd_violations_normalized_house_number"),
        table_name="hpd_violations",
    )
    op.drop_column("hpd_violations", "normalized_full_address")
    op.drop_column("hpd_violations", "normalized_street_name")
    op.drop_column("hpd_violations", "normalized_house_number")
