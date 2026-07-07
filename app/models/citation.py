import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Citation(Base):
    __tablename__ = "citations"

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
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    section_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("sections.id", ondelete="SET NULL"),
        index=True,
    )
    chunk_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("chunks.id", ondelete="SET NULL"),
        index=True,
    )
    citation_text: Mapped[str] = mapped_column(String(255))
    normalized_citation: Mapped[str] = mapped_column(String(255), index=True)
    citation_type: Mapped[str] = mapped_column(String(80))

