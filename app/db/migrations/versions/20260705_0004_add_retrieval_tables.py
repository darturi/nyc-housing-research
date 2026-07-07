"""Add retrieval tables.

Revision ID: 20260705_0004
Revises: 20260703_0003
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0004"
down_revision: str | Sequence[str] | None = "20260703_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_available_extensions
                    WHERE name = 'vector'
                ) THEN
                    CREATE EXTENSION IF NOT EXISTS vector;
                END IF;
            END
            $$;
            """
        )

    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=36), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("embedding_provider", sa.String(length=80), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
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
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chunk_id",
            "embedding_model",
            name="uq_chunk_embedding_model",
        ),
    )
    op.create_index(
        op.f("ix_chunk_embeddings_chunk_id"),
        "chunk_embeddings",
        ["chunk_id"],
    )
    op.create_index(
        op.f("ix_chunk_embeddings_embedding_model"),
        "chunk_embeddings",
        ["embedding_model"],
    )
    op.create_index(
        op.f("ix_chunk_embeddings_text_hash"),
        "chunk_embeddings",
        ["text_hash"],
    )

    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("retrieval_mode", sa.String(length=50), nullable=False),
        sa.Column("returned_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
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
        op.f("ix_retrieval_logs_query_hash"),
        "retrieval_logs",
        ["query_hash"],
    )
    op.create_index(op.f("ix_retrieval_logs_user_id"), "retrieval_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_retrieval_logs_user_id"), table_name="retrieval_logs")
    op.drop_index(op.f("ix_retrieval_logs_query_hash"), table_name="retrieval_logs")
    op.drop_table("retrieval_logs")

    op.drop_index(op.f("ix_chunk_embeddings_text_hash"), table_name="chunk_embeddings")
    op.drop_index(
        op.f("ix_chunk_embeddings_embedding_model"),
        table_name="chunk_embeddings",
    )
    op.drop_index(op.f("ix_chunk_embeddings_chunk_id"), table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
