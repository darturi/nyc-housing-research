"""Add rate limit tables.

Revision ID: 20260705_0006
Revises: 20260705_0005
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0006"
down_revision: str | Sequence[str] | None = "20260705_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope", sa.String(length=40), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("endpoint", sa.String(length=120), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("ip_address", sa.String(length=80), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rate_limit_events_lookup",
        "rate_limit_events",
        ["scope", "key", "event_type", "created_at"],
    )
    op.create_index(
        op.f("ix_rate_limit_events_created_at"),
        "rate_limit_events",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_rate_limit_events_event_type"),
        "rate_limit_events",
        ["event_type"],
    )
    op.create_index(
        op.f("ix_rate_limit_events_ip_address"),
        "rate_limit_events",
        ["ip_address"],
    )
    op.create_index(op.f("ix_rate_limit_events_key"), "rate_limit_events", ["key"])
    op.create_index(
        op.f("ix_rate_limit_events_scope"),
        "rate_limit_events",
        ["scope"],
    )
    op.create_index(
        op.f("ix_rate_limit_events_user_id"),
        "rate_limit_events",
        ["user_id"],
    )

    op.create_table(
        "user_quotas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("search_requests_per_hour", sa.Integer(), nullable=True),
        sa.Column("answer_requests_per_hour", sa.Integer(), nullable=True),
        sa.Column("daily_llm_token_budget", sa.Integer(), nullable=True),
        sa.Column("is_rate_limit_exempt", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_user_quotas_user_id"), "user_quotas", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_user_quotas_user_id"), table_name="user_quotas")
    op.drop_table("user_quotas")

    op.drop_index(op.f("ix_rate_limit_events_user_id"), table_name="rate_limit_events")
    op.drop_index(op.f("ix_rate_limit_events_scope"), table_name="rate_limit_events")
    op.drop_index(op.f("ix_rate_limit_events_key"), table_name="rate_limit_events")
    op.drop_index(
        op.f("ix_rate_limit_events_ip_address"),
        table_name="rate_limit_events",
    )
    op.drop_index(
        op.f("ix_rate_limit_events_event_type"),
        table_name="rate_limit_events",
    )
    op.drop_index(
        op.f("ix_rate_limit_events_created_at"),
        table_name="rate_limit_events",
    )
    op.drop_index("ix_rate_limit_events_lookup", table_name="rate_limit_events")
    op.drop_table("rate_limit_events")
