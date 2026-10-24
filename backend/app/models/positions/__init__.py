"""Real-position ledger models (standalone SQLite storage).

This ledger is a manually kept record of actual holdings bought through an
external brokerage terminal. It never connects to a broker and never
generates orders; it only records facts the user enters by hand.

Money columns use integer minor units of 0.0001 CNY (``*_quants``), matching
``MONEY_QUANTUM`` used across the DuckDB domains. SQLite has no exact
DECIMAL type, so floats are off-limits for these columns by project rule.
"""

from datetime import date

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.positions_base import PositionsBase
from app.models.common import TimestampMixin


class PositionBook(TimestampMixin, PositionsBase):
    __tablename__ = "position_book"
    __table_args__ = (
        UniqueConstraint("name", name="uq_position_book_name"),
        CheckConstraint("cash_quants >= 0", name="ck_position_book_cash_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cash_quants: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    note: Mapped[str | None] = mapped_column(String(200))


class PositionTrade(TimestampMixin, PositionsBase):
    __tablename__ = "position_trade"
    __table_args__ = (
        CheckConstraint("side in ('buy', 'sell')", name="ck_position_trade_side"),
        CheckConstraint("quantity > 0", name="ck_position_trade_quantity_positive"),
        CheckConstraint("price_quants > 0", name="ck_position_trade_price_positive"),
        CheckConstraint("fee_quants >= 0", name="ck_position_trade_fee_non_negative"),
        Index("ix_position_trade_book_date", "book_id", "trade_date"),
        Index("uq_position_trade_idempotency_key", "idempotency_key", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    book_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("position_book.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_quants: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee_quants: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
