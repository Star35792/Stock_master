"""add pretrade risk checks

Revision ID: 20260717_0011
Revises: 20260717_0010
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0011"
down_revision: str | None = "20260717_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS risk")
    op.create_table(
        "risk_check",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("current_drawdown", sa.Numeric(18, 10), nullable=False),
        sa.Column("limits", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["portfolio.portfolio_plan.id"]),
        schema="risk",
    )
    op.create_index(
        "ix_risk_check_plan",
        "risk_check",
        ["plan_id"],
        schema="risk",
    )
    op.create_table(
        "risk_check_order",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("check_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("order_quantity", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column(
            "rejection_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "rule_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["check_id"], ["risk.risk_check.id"]),
        schema="risk",
    )
    op.create_index(
        "ix_risk_check_order_check",
        "risk_check_order",
        ["check_id"],
        schema="risk",
    )


def downgrade() -> None:
    op.drop_table("risk_check_order", schema="risk")
    op.drop_table("risk_check", schema="risk")
    op.execute("DROP SCHEMA IF EXISTS risk")
