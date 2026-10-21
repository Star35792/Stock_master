from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RawDailyBar(Base):
    __tablename__ = "daily_bar"
    __table_args__ = (
        CheckConstraint(
            "adjust_type in ('none', 'qfq', 'hfq')",
            name="ck_raw_daily_bar_adjust_type",
        ),
        CheckConstraint(
            "source_priority >= 0",
            name="ck_raw_daily_bar_source_priority_non_negative",
        ),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("raw_daily_bar_id_seq"),
        server_default=text("nextval('raw_daily_bar_id_seq')"),
        primary_key=True,
    )
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_symbol: Mapped[str | None] = mapped_column(String(30))
    security_type: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_type: Mapped[str] = mapped_column(String(10), nullable=False)
    open: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    pct_change: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4))
    turnover_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    amplitude: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    limit_up: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    limit_down: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    is_suspended: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    special_treatment_status: Mapped[str | None] = mapped_column(String(20))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    row_hash: Mapped[str | None] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
