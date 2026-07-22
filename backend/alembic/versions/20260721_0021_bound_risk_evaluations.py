"""bind risk evaluations to server-side snapshots

Revision ID: 20260721_0021
Revises: 20260721_0020
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260721_0021"
down_revision: str | None = "20260721_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("account_id", sa.BigInteger(), nullable=True),
        sa.Column("equity_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("plan_request_hash", sa.String(length=64), nullable=True),
        sa.Column("readiness_account_hash", sa.String(length=64), nullable=True),
        sa.Column("readiness_market_hash", sa.String(length=64), nullable=True),
        sa.Column("readiness_hash", sa.String(length=64), nullable=True),
        sa.Column("readiness_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("adv_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("adv_inputs", postgresql.JSONB(), nullable=True),
        sa.Column("risk_policy_hash", sa.String(length=64), nullable=True),
        sa.Column("evaluation_hash", sa.String(length=64), nullable=True),
        sa.Column("evaluation_status", sa.String(length=20), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
    )
    for column in columns:
        op.add_column("risk_check", column, schema="risk")
    op.create_foreign_key(
        "fk_risk_check_account_id_paper_account",
        "risk_check",
        "paper_account",
        ["account_id"],
        ["id"],
        source_schema="risk",
        referent_schema="trade",
    )
    op.create_foreign_key(
        "fk_risk_check_equity_snapshot_id",
        "risk_check",
        "paper_account_equity_snapshot",
        ["equity_snapshot_id"],
        ["id"],
        source_schema="risk",
        referent_schema="trade",
    )
    op.create_check_constraint(
        "ck_risk_check_evaluation_status",
        "risk_check",
        "evaluation_status is null or evaluation_status in "
        "('passed', 'blocked', 'failed', 'legacy_unbound')",
        schema="risk",
    )
    op.create_index(
        "uq_risk_check_idempotency_key",
        "risk_check",
        ["idempotency_key"],
        unique=True,
        schema="risk",
    )
    op.create_index(
        "ix_risk_check_account",
        "risk_check",
        ["account_id"],
        schema="risk",
    )
    op.create_index(
        "ix_risk_check_equity_snapshot",
        "risk_check",
        ["equity_snapshot_id"],
        schema="risk",
    )
    op.create_index(
        "uq_risk_check_evaluation_hash",
        "risk_check",
        ["evaluation_hash"],
        unique=True,
        schema="risk",
    )

    order_columns = (
        sa.Column("reference_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("current_symbol_weight", sa.Numeric(18, 10), nullable=True),
        sa.Column("projected_symbol_weight", sa.Numeric(18, 10), nullable=True),
        sa.Column("current_industry_weight", sa.Numeric(18, 10), nullable=True),
        sa.Column("projected_industry_weight", sa.Numeric(18, 10), nullable=True),
        sa.Column("average_daily_amount", sa.Numeric(24, 4), nullable=True),
        sa.Column("adv_window", sa.Integer(), nullable=True),
        sa.Column("adv_observation_count", sa.Integer(), nullable=True),
        sa.Column("adv_input_hash", sa.String(length=64), nullable=True),
    )
    for column in order_columns:
        op.add_column("risk_check_order", column, schema="risk")


def downgrade() -> None:
    for column_name in (
        "adv_input_hash",
        "adv_observation_count",
        "adv_window",
        "average_daily_amount",
        "projected_industry_weight",
        "current_industry_weight",
        "projected_symbol_weight",
        "current_symbol_weight",
        "reference_price",
    ):
        op.drop_column("risk_check_order", column_name, schema="risk")
    op.drop_index(
        "ix_risk_check_equity_snapshot",
        table_name="risk_check",
        schema="risk",
    )
    op.drop_index(
        "ix_risk_check_account",
        table_name="risk_check",
        schema="risk",
    )
    op.drop_index(
        "uq_risk_check_evaluation_hash",
        table_name="risk_check",
        schema="risk",
    )
    op.drop_index(
        "uq_risk_check_idempotency_key",
        table_name="risk_check",
        schema="risk",
    )
    op.drop_constraint(
        "ck_risk_check_evaluation_status",
        "risk_check",
        schema="risk",
        type_="check",
    )
    op.drop_constraint(
        "fk_risk_check_equity_snapshot_id",
        "risk_check",
        schema="risk",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_risk_check_account_id_paper_account",
        "risk_check",
        schema="risk",
        type_="foreignkey",
    )
    for column_name in (
        "request_hash",
        "idempotency_key",
        "evaluation_status",
        "evaluation_hash",
        "risk_policy_hash",
        "adv_inputs",
        "adv_snapshot_hash",
        "readiness_snapshot",
        "readiness_hash",
        "readiness_market_hash",
        "readiness_account_hash",
        "plan_request_hash",
        "equity_snapshot_id",
        "account_id",
    ):
        op.drop_column("risk_check", column_name, schema="risk")
