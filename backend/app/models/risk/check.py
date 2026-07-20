from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class RiskCheck(CreatedAtMixin, Base):
    __tablename__ = "risk_check"
    __table_args__ = (
        Index("ix_risk_check_plan", "plan_id"),
        {"schema": "risk"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio.portfolio_plan.id"), nullable=False
    )
    current_drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)


class RiskCheckOrder(CreatedAtMixin, Base):
    __tablename__ = "risk_check_order"
    __table_args__ = (
        Index("ix_risk_check_order_check", "check_id"),
        {"schema": "risk"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    check_id: Mapped[int] = mapped_column(
        ForeignKey("risk.risk_check.id"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
