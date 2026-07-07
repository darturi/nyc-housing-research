"""Add optional pgvector embedding column.

Revision ID: 20260705_0007
Revises: 20260705_0006
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260705_0007"
down_revision: str | Sequence[str] | None = "20260705_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

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
                ALTER TABLE chunk_embeddings
                    ADD COLUMN IF NOT EXISTS embedding_vector vector;
                UPDATE chunk_embeddings
                SET embedding_vector = embedding::text::vector
                WHERE embedding_vector IS NULL
                  AND embedding IS NOT NULL;
            END IF;
            CREATE INDEX IF NOT EXISTS ix_chunks_fts_search
                ON chunks
                USING GIN (
                    (
                        setweight(to_tsvector('english', COALESCE(title, '')), 'A')
                        || setweight(
                            to_tsvector('english', COALESCE(citation, '')),
                            'A'
                        )
                        || setweight(to_tsvector('english', COALESCE(text, '')), 'B')
                    )
                );
        END
        $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    op.execute(
        """
        DROP INDEX IF EXISTS ix_chunks_fts_search;
        ALTER TABLE chunk_embeddings
            DROP COLUMN IF EXISTS embedding_vector;
        """
    )
