from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class PortfolioPlanRecord(CreatedAtMixin, Base):
    __tablename__ = "portfolio_plan"
    __table_args__ = (
        Index("ix_portfolio_plan_as_of_date", "as_of_date"),
        Index(
            "uq_portfolio_plan_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
        {"schema": "portfolio"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("portfolio_portfolio_plan_id_seq"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    cash_before: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    target_cash: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    cash_after: Mapped[Decimal] = mapped_column(Numeric(24, 4), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class PortfolioTargetRecord(CreatedAtMixin, Base):
    __tablename__ = "portfolio_target"
    __table_args__ = (
        UniqueConstraint("plan_id", "symbol"),
        Index("ix_portfolio_target_plan", "plan_id"),
        {"schema": "portfolio"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("portfolio_portfolio_target_id_seq"), primary_key=True
    )
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
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
    score: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
