"""Add answer logs.

Revision ID: 20260705_0005
Revises: 20260705_0004
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0005"
down_revision: str | Sequence[str] | None = "20260705_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("retrieval_log_id", sa.String(length=36), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("question_hash", sa.String(length=64), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("cited_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("answer_status", sa.String(length=40), nullable=False),
        sa.Column("llm_provider", sa.String(length=80), nullable=False),
        sa.Column("llm_model", sa.String(length=120), nullable=False),
        sa.Column("prompt_token_count", sa.Integer(), nullable=True),
        sa.Column("completion_token_count", sa.Integer(), nullable=True),
        sa.Column("total_token_count", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["retrieval_log_id"],
            ["retrieval_logs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_answer_logs_answer_status"),
        "answer_logs",
        ["answer_status"],
    )
    op.create_index(op.f("ix_answer_logs_created_at"), "answer_logs", ["created_at"])
    op.create_index(
        op.f("ix_answer_logs_question_hash"),
        "answer_logs",
        ["question_hash"],
    )
    op.create_index(
        op.f("ix_answer_logs_retrieval_log_id"),
        "answer_logs",
        ["retrieval_log_id"],
    )
    op.create_index(op.f("ix_answer_logs_user_id"), "answer_logs", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_answer_logs_user_id"), table_name="answer_logs")
    op.drop_index(
        op.f("ix_answer_logs_retrieval_log_id"),
        table_name="answer_logs",
    )
    op.drop_index(op.f("ix_answer_logs_question_hash"), table_name="answer_logs")
    op.drop_index(op.f("ix_answer_logs_created_at"), table_name="answer_logs")
    op.drop_index(op.f("ix_answer_logs_answer_status"), table_name="answer_logs")
    op.drop_table("answer_logs")
