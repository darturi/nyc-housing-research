"""Add artifact retention metadata.

Revision ID: 20260713_0009
Revises: 20260712_0008
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0009"
down_revision: str | Sequence[str] | None = "20260712_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_versions",
        sa.Column("artifact_retained_until", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "source_versions",
        sa.Column("artifact_purged_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        op.f("ix_source_versions_artifact_retained_until"),
        "source_versions",
        ["artifact_retained_until"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_source_versions_artifact_retained_until"),
        table_name="source_versions",
    )
    op.drop_column("source_versions", "artifact_purged_at")
    op.drop_column("source_versions", "artifact_retained_until")
