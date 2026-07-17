from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issue"
    __table_args__ = (
        CheckConstraint(
            "severity in ('info', 'warning', 'error', 'critical')",
            name="ck_quality_issue_severity",
        ),
        CheckConstraint(
            "status in ('open', 'ignored', 'fixed', 'reopened', 'false_positive')",
            name="ck_quality_issue_status_allowed",
        ),
        Index("ix_quality_issue_status", "status"),
        Index("ix_quality_issue_symbol_date", "symbol", "trade_date"),
        Index("ix_quality_issue_symbol_status_date", "symbol", "status", "trade_date"),
        Index("ix_quality_issue_status_severity_created", "status", "severity", "created_at"),
        Index("ix_quality_issue_type", "issue_type"),
        Index("ix_quality_issue_severity", "severity"),
        {"schema": "system"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(20))
    trade_date: Mapped[date | None] = mapped_column(Date)
    adjust_type: Mapped[str | None] = mapped_column(String(10))
    source_name: Mapped[str | None] = mapped_column(String(50))
    raw_id: Mapped[int | None] = mapped_column(ForeignKey("raw.daily_bar.id"))
    issue_type: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_value: Mapped[dict | None] = mapped_column(JSONB)
    actual_value: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'open'"))
    resolved_by: Mapped[str | None] = mapped_column(String(50))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
