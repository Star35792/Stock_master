from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class JobLog(Base):
    __tablename__ = "job_log"
    __table_args__ = (
        CheckConstraint(
            "job_type in ('import', 'normalize', 'validate', 'reconcile', 'repair', 'schedule')",
            name="ck_job_log_type",
        ),
        CheckConstraint(
            "status in ('running', 'success', 'failed', 'partial')",
            name="ck_job_log_status",
        ),
        CheckConstraint(
            "rows_read >= 0 and rows_inserted >= 0 and rows_updated >= 0 and rows_failed >= 0",
            name="ck_job_log_counts_non_negative",
        ),
        CheckConstraint(
            "finished_at is null or finished_at >= started_at",
            name="ck_job_log_time_order",
        ),
        Index("ix_job_log_type_status_started", "job_type", "status", "started_at"),
        Index("ix_job_log_batch_id", "batch_id"),
        {"schema": "system"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("raw.import_batch.id"))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )
    rows_read: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_inserted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rows_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
