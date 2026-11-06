from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import CreatedAtMixin


class RiskCheck(CreatedAtMixin, Base):
    __tablename__ = "risk_check"
    __table_args__ = (
        Index("ix_risk_check_plan", "plan_id"),
        Index("ix_risk_check_account", "account_id"),
        Index("ix_risk_check_equity_snapshot", "equity_snapshot_id"),
        Index("uq_risk_check_idempotency_key", "idempotency_key", unique=True),
        Index("uq_risk_check_evaluation_hash", "evaluation_hash", unique=True),
        CheckConstraint(
            "evaluation_status is null or evaluation_status in "
            "('passed', 'blocked', 'failed', 'legacy_unbound')",
            name="ck_risk_check_evaluation_status",
        ),
        {"schema": "risk"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("risk_risk_check_id_seq"),
        server_default=text("nextval('risk_risk_check_id_seq')"), primary_key=True
    )
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_drawdown: Mapped[Decimal] = mapped_column(Numeric(18, 10), nullable=False)
    limits: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    account_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    equity_snapshot_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    plan_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_account_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_market_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    readiness_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    adv_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adv_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    risk_policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluation_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluation_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RiskCheckOrder(CreatedAtMixin, Base):
    __tablename__ = "risk_check_order"
    __table_args__ = (
        Index("ix_risk_check_order_check", "check_id"),
        {"schema": "risk"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("risk_risk_check_order_id_seq"),
        server_default=text("nextval('risk_risk_check_order_id_seq')"), primary_key=True
    )
    check_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rejection_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rule_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    current_symbol_weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    projected_symbol_weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    current_industry_weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    projected_industry_weight: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 10), nullable=True
    )
    average_daily_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    adv_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adv_observation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adv_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
