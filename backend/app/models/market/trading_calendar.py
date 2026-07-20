from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, Index, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class TradingCalendar(TimestampMixin, Base):
    __tablename__ = "trading_calendar"
    __table_args__ = (
        UniqueConstraint("exchange", "trade_date"),
        CheckConstraint(
            "exchange in ('SH', 'SZ', 'BJ', 'HK', 'UNKNOWN')",
            name="ck_trading_calendar_exchange",
        ),
        Index("ix_trading_calendar_trade_date", "trade_date"),
        Index("ix_trading_calendar_exchange_open", "exchange", "is_open"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_open: Mapped[bool] = mapped_column(nullable=False)
    previous_trade_date: Mapped[date | None] = mapped_column(Date)
    next_trade_date: Mapped[date | None] = mapped_column(Date)
    is_half_day: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    source_name: Mapped[str | None] = mapped_column(String(50))
