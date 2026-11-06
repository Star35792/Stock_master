from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class FactorRun(CreatedAtMixin, Base):
    __tablename__ = "factor_run"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'success', 'failed', 'partial')",
            name="ck_factor_run_status",
        ),
        CheckConstraint("rows_written >= 0", name="ck_factor_run_rows_written"),
        CheckConstraint(
            "input_start_date is null or input_end_date is null or "
            "input_start_date <= input_end_date",
            name="ck_factor_run_input_date_order",
        ),
        CheckConstraint(
            "finished_at is null or finished_at >= started_at",
            name="ck_factor_run_time_order",
        ),
        Index("ix_factor_run_factor_status_started", "factor_name", "status", "started_at"),
        Index("ix_factor_run_definition", "definition_id"),
        {"schema": "factor"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("factor_factor_run_id_seq"),
        server_default=text("nextval('factor_factor_run_id_seq')"), primary_key=True
    )
    definition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    release_id: Mapped[str | None] = mapped_column(String(64))
    input_start_date: Mapped[date | None] = mapped_column(Date)
    input_end_date: Mapped[date | None] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'running'")
    )
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON)
