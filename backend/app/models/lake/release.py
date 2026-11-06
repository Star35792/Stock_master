from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    Sequence,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Release(Base):
    __tablename__ = "release"
    __table_args__ = (
        UniqueConstraint("release_id", name="uq_release_release_id"),
        CheckConstraint(
            "quality_status in ('pending', 'trusted', 'rejected')",
            name="ck_release_quality_status",
        ),
        CheckConstraint("row_count >= 0", name="ck_release_row_count"),
        CheckConstraint(
            "min_date is null or max_date is null or min_date <= max_date",
            name="ck_release_date_order",
        ),
        {"schema": "lake"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("lake_release_id_seq"),
        server_default=text("nextval('lake_release_id_seq')"),
        primary_key=True,
    )
    release_id: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    min_date: Mapped[date | None] = mapped_column(Date)
    max_date: Mapped[date | None] = mapped_column(Date)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_release_id: Mapped[str | None] = mapped_column(String(64))
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    quality_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
