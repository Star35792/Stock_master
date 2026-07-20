from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class DailyBarSourceCompare(Base):
    __tablename__ = "daily_bar_source_compare"
    __table_args__ = (
        CheckConstraint("raw_id_a <> raw_id_b", name="ck_daily_bar_compare_distinct_raw"),
        Index("ix_daily_bar_compare_symbol_date", "symbol", "trade_date", "adjust_type"),
        Index("ix_daily_bar_compare_conflict", "is_conflict"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_type: Mapped[str] = mapped_column(String(10), nullable=False)
    source_a: Mapped[str] = mapped_column(String(50), nullable=False)
    source_b: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_id_a: Mapped[int] = mapped_column(ForeignKey("raw.daily_bar.id"), nullable=False)
    raw_id_b: Mapped[int] = mapped_column(ForeignKey("raw.daily_bar.id"), nullable=False)
    close_diff: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    close_diff_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    volume_diff_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    amount_diff_pct: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    is_conflict: Mapped[bool] = mapped_column(nullable=False)
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
