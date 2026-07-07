import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnswerLog(Base):
    __tablename__ = "answer_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    retrieval_log_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("retrieval_logs.id", ondelete="SET NULL"),
        index=True,
    )
    question_text: Mapped[str] = mapped_column(Text)
    question_hash: Mapped[str] = mapped_column(String(64), index=True)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON)
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(JSON)
    cited_chunk_ids: Mapped[list[str]] = mapped_column(JSON)
    answer_text: Mapped[str] = mapped_column(Text)
    answer_status: Mapped[str] = mapped_column(String(40), index=True)
    llm_provider: Mapped[str] = mapped_column(String(80))
    llm_model: Mapped[str] = mapped_column(String(120))
    prompt_token_count: Mapped[int | None] = mapped_column(Integer)
    completion_token_count: Mapped[int | None] = mapped_column(Integer)
    total_token_count: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )
