from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class DailyBar(TimestampMixin, Base):
    __tablename__ = "daily_bar"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "adjust_type"),
        ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        CheckConstraint(
            "adjust_type in ('none', 'qfq', 'hfq')",
            name="ck_daily_bar_adjust_type",
        ),
        CheckConstraint(
            "quality_status in ('trusted', 'suspect', 'rejected', 'manual_fixed')",
            name="ck_daily_bar_quality_status_allowed",
        ),
        CheckConstraint(
            "quality_score >= 0 and quality_score <= 100",
            name="ck_daily_bar_quality_score_range",
        ),
        CheckConstraint("source_count >= 1", name="ck_daily_bar_source_count_positive"),
        CheckConstraint(
            "open > 0 and high > 0 and low > 0 and close > 0",
            name="ck_daily_bar_ohlc_positive",
        ),
        CheckConstraint(
            "high >= open and high >= close and high >= low and "
            "low <= open and low <= close and low <= high",
            name="ck_daily_bar_ohlc_order",
        ),
        CheckConstraint(
            "volume is null or volume >= 0",
            name="ck_daily_bar_volume_non_negative",
        ),
        CheckConstraint(
            "amount is null or amount >= 0",
            name="ck_daily_bar_amount_non_negative",
        ),
        Index("ix_daily_bar_symbol_adjust_date", "symbol", "adjust_type", "trade_date"),
        Index("ix_daily_bar_trade_date_type", "trade_date", "security_type"),
        Index("ix_daily_bar_quality_status", "quality_status"),
        Index(
            "ix_daily_bar_status_symbol_adjust_date",
            "quality_status",
            "symbol",
            "adjust_type",
            "trade_date",
        ),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    security_type: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_type: Mapped[str] = mapped_column(String(10), nullable=False)
    open: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    amplitude: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    quality_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0")
    )
    quality_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'suspect'")
    )
    is_suspect: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    selected_raw_id: Mapped[int | None] = mapped_column(ForeignKey("raw.daily_bar.id"))
