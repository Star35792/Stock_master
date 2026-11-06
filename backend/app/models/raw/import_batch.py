from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ImportBatch(Base):
    __tablename__ = "import_batch"
    __table_args__ = (
        UniqueConstraint("batch_no"),
        CheckConstraint(
            "status in ('running', 'success', 'failed', 'partial')",
            name="ck_raw_import_batch_status",
        ),
        CheckConstraint(
            "row_count >= 0 and accepted_count >= 0 and rejected_count >= 0",
            name="ck_raw_import_batch_counts_non_negative",
        ),
        CheckConstraint(
            "accepted_count + rejected_count <= row_count",
            name="ck_raw_import_batch_counts_consistent",
        ),
        CheckConstraint(
            "finished_at is null or finished_at >= started_at",
            name="ck_raw_import_batch_time_order",
        ),
        Index("ix_import_batch_source_dataset", "source_name", "dataset"),
        Index("ix_import_batch_status", "status"),
        Index("ix_import_batch_started_at", "started_at"),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("raw_import_batch_id_seq"),
        server_default=text("nextval('raw_import_batch_id_seq')"), primary_key=True
    )
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    file_path: Mapped[str | None] = mapped_column(Text)
    request_params: Mapped[dict | None] = mapped_column(JSON)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
