from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class PaperAccount(TimestampMixin, Base):
    __tablename__ = "paper_account"
    __table_args__ = (
        Index("ix_paper_account_name", "name", unique=True),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)


class PaperOrder(TimestampMixin, Base):
    __tablename__ = "paper_order"
    __table_args__ = (
        Index("ix_paper_order_account_date", "account_id", "trade_date"),
        Index("ix_paper_order_check", "check_id"),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("trade.paper_account.id"), nullable=False
    )
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio.portfolio_plan.id"), nullable=False
    )
    check_id: Mapped[int] = mapped_column(ForeignKey("risk.risk_check.id"), nullable=False)
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
        Index("ix_paper_fill_order", "order_id"),
        Index("ix_paper_fill_account_date", "account_id", "trade_date"),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("trade.paper_order.id"), nullable=False)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("trade.paper_account.id"), nullable=False
    )
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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey("trade.paper_account.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
