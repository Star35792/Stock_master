"""add paper execution approval intent records

Revision ID: 20260721_0022
Revises: 20260721_0021
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260721_0022"
down_revision: str | None = "20260721_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_execution_approval",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("risk_check_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("equity_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("approval_scope", sa.String(length=30), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("approved_by", sa.String(length=100), nullable=False),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column("evaluation_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_request_hash", sa.String(length=64), nullable=False),
        sa.Column("equity_valuation_hash", sa.String(length=64), nullable=False),
        sa.Column("readiness_account_hash", sa.String(length=64), nullable=False),
        sa.Column("readiness_market_hash", sa.String(length=64), nullable=False),
        sa.Column("readiness_hash", sa.String(length=64), nullable=False),
        sa.Column("adv_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("risk_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("order_batch_hash", sa.String(length=64), nullable=False),
        sa.Column("approval_hash", sa.String(length=64), nullable=False),
        sa.Column("order_summary", postgresql.JSONB(), nullable=False),
        sa.Column("confirmation_policy", postgresql.JSONB(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("revocation_idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("revocation_request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status in ('pending', 'consumed', 'expired', 'revoked')",
            name="ck_paper_execution_approval_status",
        ),
        sa.CheckConstraint(
            "approval_scope = 'paper_execution'",
            name="ck_paper_execution_approval_scope",
        ),
        sa.CheckConstraint(
            "actor_type = 'local_single_user'",
            name="ck_paper_execution_approval_actor",
        ),
        sa.CheckConstraint(
            "expires_at > approved_at",
            name="ck_paper_execution_approval_expiry",
        ),
        sa.CheckConstraint(
            "(status = 'pending' and consumed_at is null and expired_at is null and revoked_at is null) or "
            "(status = 'consumed' and consumed_at is not null and expired_at is null and revoked_at is null) or "
            "(status = 'expired' and consumed_at is null and expired_at is not null and revoked_at is null) or "
            "(status = 'revoked' and consumed_at is null and expired_at is null and revoked_at is not null)",
            name="ck_paper_execution_approval_terminal_time",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["trade.paper_account.id"]),
        sa.ForeignKeyConstraint(
            ["equity_snapshot_id"],
            ["trade.paper_account_equity_snapshot.id"],
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["portfolio.portfolio_plan.id"]),
        sa.ForeignKeyConstraint(["risk_check_id"], ["risk.risk_check.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("risk_check_id"),
        schema="trade",
    )
    op.create_index(
        "uq_paper_execution_approval_idempotency",
        "paper_execution_approval",
        ["idempotency_key"],
        unique=True,
        schema="trade",
    )
    op.create_index(
        "ix_paper_execution_approval_status_expiry",
        "paper_execution_approval",
        ["status", "expires_at"],
        schema="trade",
    )
    op.create_index(
        "uq_paper_execution_approval_revocation_idempotency",
        "paper_execution_approval",
        ["revocation_idempotency_key"],
        unique=True,
        schema="trade",
    )
    op.create_index(
        "ix_paper_execution_approval_account_created",
        "paper_execution_approval",
        ["account_id", "created_at"],
        schema="trade",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_execution_approval_account_created",
        table_name="paper_execution_approval",
        schema="trade",
    )
    op.drop_index(
        "uq_paper_execution_approval_revocation_idempotency",
        table_name="paper_execution_approval",
        schema="trade",
    )
    op.drop_index(
        "ix_paper_execution_approval_status_expiry",
        table_name="paper_execution_approval",
        schema="trade",
    )
    op.drop_index(
        "uq_paper_execution_approval_idempotency",
        table_name="paper_execution_approval",
        schema="trade",
    )
    op.drop_table("paper_execution_approval", schema="trade")
