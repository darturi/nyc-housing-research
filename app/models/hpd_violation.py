import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class HpdViolation(Base):
    __tablename__ = "hpd_violations"

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
    external_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    building_id: Mapped[str | None] = mapped_column(String(120), index=True)
    registration_id: Mapped[str | None] = mapped_column(String(120), index=True)
    boro: Mapped[str | None] = mapped_column(String(80))
    house_number: Mapped[str | None] = mapped_column(String(80), index=True)
    normalized_house_number: Mapped[str | None] = mapped_column(
        String(80), index=True
    )
    street_name: Mapped[str | None] = mapped_column(String(255), index=True)
    normalized_street_name: Mapped[str | None] = mapped_column(
        String(255), index=True
    )
    normalized_full_address: Mapped[str | None] = mapped_column(
        String(400), index=True
    )
    zip_code: Mapped[str | None] = mapped_column(String(20), index=True)
    apartment: Mapped[str | None] = mapped_column(String(80))
    violation_class: Mapped[str | None] = mapped_column("class", String(20))
    inspection_date: Mapped[date | None] = mapped_column(Date)
    approved_date: Mapped[date | None] = mapped_column(Date)
    original_certify_by_date: Mapped[date | None] = mapped_column(Date)
    original_correct_by_date: Mapped[date | None] = mapped_column(Date)
    new_certify_by_date: Mapped[date | None] = mapped_column(Date)
    new_correct_by_date: Mapped[date | None] = mapped_column(Date)
    certified_date: Mapped[date | None] = mapped_column(Date)
    order_number: Mapped[str | None] = mapped_column(String(120))
    nov_id: Mapped[str | None] = mapped_column(String(120))
    nov_description: Mapped[str | None] = mapped_column(Text)
    current_status: Mapped[str | None] = mapped_column(String(120), index=True)
    current_status_date: Mapped[date | None] = mapped_column(Date)
    raw_record: Mapped[dict[str, Any]] = mapped_column(JSON)
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
