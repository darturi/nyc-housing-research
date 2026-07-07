import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50))
    publisher: Mapped[str] = mapped_column(String(255))
    jurisdiction: Mapped[str] = mapped_column(String(50))
    source_url: Mapped[str] = mapped_column(Text)
    access_type: Mapped[str] = mapped_column(String(80))
    license_status: Mapped[str] = mapped_column(String(120))
    terms_url: Mapped[str | None] = mapped_column(Text)
    redistribution_allowed: Mapped[bool | None] = mapped_column(Boolean)
    notes: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions = relationship(
        "SourceVersion",
        back_populates="source",
        cascade="all, delete-orphan",
    )

