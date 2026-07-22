"""add idempotent paper execution batches

Revision ID: 20260721_0023
Revises: 20260721_0022
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260721_0023"
down_revision: str | None = "20260721_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_execution_batch",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("approval_id", sa.BigInteger(), nullable=False),
        sa.Column("risk_check_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("equity_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("execution_mode", sa.String(length=20), nullable=False),
        sa.Column("actor_type", sa.String(length=30), nullable=False),
        sa.Column("executed_by", sa.String(length=100), nullable=False),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column("approval_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluation_hash", sa.String(length=64), nullable=False),
        sa.Column("order_batch_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("account_state_hash_before", sa.String(length=64), nullable=False),
        sa.Column("account_state_hash_after", sa.String(length=64), nullable=True),
        sa.Column("execution_hash", sa.String(length=64), nullable=False),
        sa.Column("cash_before", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("cash_after", sa.Numeric(precision=24, scale=4), nullable=True),
        sa.Column("order_count", sa.Integer(), nullable=False),
        sa.Column("fill_count", sa.Integer(), nullable=False),
        sa.Column("total_commission", sa.Numeric(precision=24, scale=6), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("execution_policy", postgresql.JSONB(), nullable=False),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
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
            "status in ('completed', 'rejected')",
            name="ck_paper_execution_batch_status",
        ),
        sa.CheckConstraint(
            "execution_mode = 'paper'",
            name="ck_paper_execution_batch_mode",
        ),
        sa.CheckConstraint(
            "actor_type = 'local_single_user'",
            name="ck_paper_execution_batch_actor",
        ),
        sa.CheckConstraint(
            "(status = 'completed' and failure_code is null and order_count > 0 and "
            "fill_count = order_count and cash_before is not null and cash_after is not null and "
            "account_state_hash_after is not null) or "
            "(status = 'rejected' and failure_code is not null and order_count > 0 and "
            "fill_count = 0 and cash_before is not null and cash_after is not null and "
            "account_state_hash_after is not null and cash_before = cash_after and "
            "account_state_hash_before = account_state_hash_after)",
            name="ck_paper_execution_batch_result",
        ),
        sa.CheckConstraint(
            "order_count >= 0 and fill_count >= 0 and fill_count <= order_count and "
            "total_commission >= 0",
            name="ck_paper_execution_batch_counts",
        ),
        sa.CheckConstraint(
            "started_at <= completed_at",
            name="ck_paper_execution_batch_time_order",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["trade.paper_account.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["trade.paper_execution_approval.id"]),
        sa.ForeignKeyConstraint(["equity_snapshot_id"], ["trade.paper_account_equity_snapshot.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["portfolio.portfolio_plan.id"]),
        sa.ForeignKeyConstraint(["risk_check_id"], ["risk.risk_check.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id"),
        schema="trade",
    )
    op.create_index(
        "uq_paper_execution_batch_idempotency",
        "paper_execution_batch",
        ["idempotency_key"],
        unique=True,
        schema="trade",
    )
    op.create_index(
        "ix_paper_execution_batch_account_created",
        "paper_execution_batch",
        ["account_id", "created_at"],
        schema="trade",
    )
    op.add_column(
        "paper_order",
        sa.Column("risk_check_order_id", sa.BigInteger(), nullable=True),
        schema="trade",
    )
    op.add_column(
        "paper_order",
        sa.Column("execution_batch_id", sa.BigInteger(), nullable=True),
        schema="trade",
    )
    op.add_column(
        "paper_order",
        sa.Column("execution_index", sa.Integer(), nullable=True),
        schema="trade",
    )
    op.create_foreign_key(
        "fk_paper_order_risk_check_order",
        "paper_order",
        "risk_check_order",
        ["risk_check_order_id"],
        ["id"],
        source_schema="trade",
        referent_schema="risk",
    )
    op.create_foreign_key(
        "fk_paper_order_execution_batch",
        "paper_order",
        "paper_execution_batch",
        ["execution_batch_id"],
        ["id"],
        source_schema="trade",
        referent_schema="trade",
    )
    op.create_unique_constraint(
        "uq_paper_order_execution_index",
        "paper_order",
        ["execution_batch_id", "execution_index"],
        schema="trade",
    )
    op.create_unique_constraint(
        "uq_paper_order_execution_risk_order",
        "paper_order",
        ["execution_batch_id", "risk_check_order_id"],
        schema="trade",
    )
    op.create_unique_constraint(
        "uq_paper_fill_order",
        "paper_fill",
        ["order_id"],
        schema="trade",
    )


def downgrade() -> None:
    op.drop_constraint("uq_paper_fill_order", "paper_fill", schema="trade", type_="unique")
    op.drop_constraint(
        "uq_paper_order_execution_risk_order",
        "paper_order",
        schema="trade",
        type_="unique",
    )
    op.drop_constraint(
        "uq_paper_order_execution_index",
        "paper_order",
        schema="trade",
        type_="unique",
    )
    op.drop_constraint(
        "fk_paper_order_execution_batch",
        "paper_order",
        schema="trade",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_paper_order_risk_check_order",
        "paper_order",
        schema="trade",
        type_="foreignkey",
    )
    op.drop_column("paper_order", "execution_index", schema="trade")
    op.drop_column("paper_order", "execution_batch_id", schema="trade")
    op.drop_column("paper_order", "risk_check_order_id", schema="trade")
    op.drop_index(
        "ix_paper_execution_batch_account_created",
        table_name="paper_execution_batch",
        schema="trade",
    )
    op.drop_index(
        "uq_paper_execution_batch_idempotency",
        table_name="paper_execution_batch",
        schema="trade",
    )
    op.drop_table("paper_execution_batch", schema="trade")
