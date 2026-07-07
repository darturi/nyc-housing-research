import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_version_id", "document_key", name="uq_document_key"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sources.id", ondelete="CASCADE"),
        index=True,
    )
    source_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("source_versions.id", ondelete="CASCADE"),
        index=True,
    )
    document_key: Mapped[str] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    document_type: Mapped[str] = mapped_column(String(80))
    jurisdiction: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    sections = relationship(
        "Section",
        back_populates="document",
        cascade="all, delete-orphan",
    )

