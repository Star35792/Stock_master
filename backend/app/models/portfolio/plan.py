from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class PortfolioPlanRecord(CreatedAtMixin, Base):
    __tablename__ = "portfolio_plan"
    __table_args__ = (
        Index("ix_portfolio_plan_as_of_date", "as_of_date"),
        {"schema": "portfolio"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    cash_before: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    target_cash: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    cash_after: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)


class PortfolioTargetRecord(CreatedAtMixin, Base):
    __tablename__ = "portfolio_target"
    __table_args__ = (
        UniqueConstraint("plan_id", "symbol"),
        Index("ix_portfolio_target_plan", "plan_id"),
        {"schema": "portfolio"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio.portfolio_plan.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    security_type: Mapped[str] = mapped_column(String(20), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_weight: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    constrained_weight: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    final_weight: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    current_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    target_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
