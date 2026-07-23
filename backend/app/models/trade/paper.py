from datetime import date, datetime
from decimal import Decimal
from typing import Any

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class PaperAccount(TimestampMixin, Base):
    __tablename__ = "paper_account"
    __table_args__ = (
        Index("ix_paper_account_name", "name", unique=True),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_account_id_seq"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)


class PaperOrder(TimestampMixin, Base):
    __tablename__ = "paper_order"
    __table_args__ = (
        UniqueConstraint(
            "execution_batch_id",
            "execution_index",
            name="uq_paper_order_execution_index",
        ),
        UniqueConstraint(
            "execution_batch_id",
            "risk_check_order_id",
            name="uq_paper_order_execution_risk_order",
        ),
        Index("ix_paper_order_account_date", "account_id", "trade_date"),
        Index("ix_paper_order_check", "check_id"),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_order_id_seq"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    check_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    risk_check_order_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    execution_batch_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    execution_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)


class PaperFill(TimestampMixin, Base):
    __tablename__ = "paper_fill"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_paper_fill_order"),
        Index("ix_paper_fill_order", "order_id"),
        Index("ix_paper_fill_account_date", "account_id", "trade_date"),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_fill_id_seq"), primary_key=True
    )
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    commission: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)


class PaperPosition(TimestampMixin, Base):
    __tablename__ = "paper_position"
    __table_args__ = (
        Index("ix_paper_position_account_symbol", "account_id", "symbol", unique=True),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_position_id_seq"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)


class PaperAccountEquitySnapshot(TimestampMixin, Base):
    __tablename__ = "paper_account_equity_snapshot"
    __table_args__ = (
        CheckConstraint(
            "history_status in ('bootstrap', 'continuous', 'gap')",
            name="ck_paper_equity_history_status",
        ),
        CheckConstraint(
            "drawdown >= -1 and drawdown <= 0",
            name="ck_paper_equity_drawdown_range",
        ),
        UniqueConstraint(
            "account_id",
            "valuation_date",
            name="uq_paper_equity_snapshot_account_date",
        ),
        Index(
            "uq_paper_equity_snapshot_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_paper_equity_snapshot_account_date",
            "account_id",
            "valuation_date",
        ),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_account_equity_snapshot_id_seq"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    valuation_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    positions_market_value: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    high_watermark: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    previous_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    monitoring_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    history_status: Mapped[str] = mapped_column(String(20), nullable=False)
    account_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    market_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valuation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    valuation_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PaperAccountEquityComponent(TimestampMixin, Base):
    __tablename__ = "paper_account_equity_component"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "symbol"),
        Index("ix_paper_equity_component_snapshot", "snapshot_id"),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_account_equity_component_id_seq"), primary_key=True
    )
    snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    market_value: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    daily_bar_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(20), nullable=False)
    bar_trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_type: Mapped[str] = mapped_column(String(10), nullable=False)
    is_suspect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    bar_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
