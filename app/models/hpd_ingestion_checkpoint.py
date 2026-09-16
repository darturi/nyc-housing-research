import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HpdIngestionCheckpoint(Base):
    """Durable state for resumable HPD violation API ingestion."""

    __tablename__ = "hpd_ingestion_checkpoints"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sources.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    run_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    last_page_key: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status_date_watermark: Mapped[date | None] = mapped_column(Date)
    artifact_manifest_uri: Mapped[str | None] = mapped_column(Text)
    source_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("source_versions.id", ondelete="SET NULL")
    )
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
