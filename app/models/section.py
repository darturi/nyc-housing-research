import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("document_id", "section_key", name="uq_section_key"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
    )
    parent_section_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("sections.id", ondelete="SET NULL"),
        index=True,
    )
    section_key: Mapped[str] = mapped_column(String(255))
    citation: Mapped[str | None] = mapped_column(String(255), index=True)
    title: Mapped[str | None] = mapped_column(String(500))
    hierarchy_path: Mapped[str] = mapped_column(String(500))
    order_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)

    document = relationship("Document", back_populates="sections")

