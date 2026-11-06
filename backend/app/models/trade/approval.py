from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class PaperExecutionApproval(TimestampMixin, Base):
    __tablename__ = "paper_execution_approval"
    __table_args__ = (
        UniqueConstraint("risk_check_id"),
        Index("uq_paper_execution_approval_idempotency", "idempotency_key", unique=True),
        Index(
            "uq_paper_execution_approval_revocation_idempotency",
            "revocation_idempotency_key",
            unique=True,
        ),
        Index("ix_paper_execution_approval_status_expiry", "status", "expires_at"),
        Index("ix_paper_execution_approval_account_created", "account_id", "created_at"),
        CheckConstraint(
            "status in ('pending', 'consumed', 'expired', 'revoked')",
            name="ck_paper_execution_approval_status",
        ),
        CheckConstraint(
            "approval_scope = 'paper_execution'",
            name="ck_paper_execution_approval_scope",
        ),
        CheckConstraint(
            "actor_type = 'local_single_user'",
            name="ck_paper_execution_approval_actor",
        ),
        CheckConstraint(
            "expires_at > approved_at",
            name="ck_paper_execution_approval_expiry",
        ),
        CheckConstraint(
            "(status = 'pending' and consumed_at is null and "
            "expired_at is null and revoked_at is null) or "
            "(status = 'consumed' and consumed_at is not null and "
            "expired_at is null and revoked_at is null) or "
            "(status = 'expired' and consumed_at is null and "
            "expired_at is not null and revoked_at is null) or "
            "(status = 'revoked' and consumed_at is null and "
            "expired_at is null and revoked_at is not null)",
            name="ck_paper_execution_approval_terminal_time",
        ),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_execution_approval_id_seq"),
        server_default=text("nextval('trade_paper_execution_approval_id_seq')"), primary_key=True
    )
    risk_check_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    equity_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_scope: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(100), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    evaluation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    equity_valuation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_account_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_market_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    readiness_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    adv_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_batch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_summary: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    confirmation_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revocation_idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revocation_request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class PaperExecutionBatch(TimestampMixin, Base):
    __tablename__ = "paper_execution_batch"
    __table_args__ = (
        UniqueConstraint("approval_id"),
        Index("uq_paper_execution_batch_idempotency", "idempotency_key", unique=True),
        Index("ix_paper_execution_batch_account_created", "account_id", "created_at"),
        CheckConstraint(
            "status in ('completed', 'rejected')",
            name="ck_paper_execution_batch_status",
        ),
        CheckConstraint(
            "execution_mode = 'paper'",
            name="ck_paper_execution_batch_mode",
        ),
        CheckConstraint(
            "actor_type = 'local_single_user'",
            name="ck_paper_execution_batch_actor",
        ),
        CheckConstraint(
            "(status = 'completed' and failure_code is null and order_count > 0 and "
            "fill_count = order_count and cash_before is not null and cash_after is not null and "
            "account_state_hash_after is not null) or "
            "(status = 'rejected' and failure_code is not null and order_count > 0 and "
            "fill_count = 0 and cash_before is not null and cash_after is not null and "
            "account_state_hash_after is not null and cash_before = cash_after and "
            "account_state_hash_before = account_state_hash_after)",
            name="ck_paper_execution_batch_result",
        ),
        CheckConstraint(
            "order_count >= 0 and fill_count >= 0 and fill_count <= order_count and "
            "total_commission >= 0",
            name="ck_paper_execution_batch_counts",
        ),
        CheckConstraint(
            "started_at <= completed_at",
            name="ck_paper_execution_batch_time_order",
        ),
        {"schema": "trade"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("trade_paper_execution_batch_id_seq"),
        server_default=text("nextval('trade_paper_execution_batch_id_seq')"), primary_key=True
    )
    approval_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    risk_check_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    plan_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    account_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    equity_snapshot_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    executed_by: Mapped[str] = mapped_column(String(100), nullable=False)
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    order_batch_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    account_state_hash_before: Mapped[str] = mapped_column(String(64), nullable=False)
    account_state_hash_after: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cash_before: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    cash_after: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), nullable=True)
    order_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_commission: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    input_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    result_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    execution_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
