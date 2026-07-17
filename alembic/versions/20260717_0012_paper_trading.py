"""add paper trading tables

Revision ID: 20260717_0012
Revises: 20260717_0011
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0012"
down_revision: str | None = "20260717_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS trade")
    op.create_table(
        "paper_account",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("cash_balance", sa.Numeric(24, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema="trade",
    )
    op.create_index(
        "ix_paper_account_name",
        "paper_account",
        ["name"],
        unique=True,
        schema="trade",
    )
    op.create_table(
        "paper_order",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("check_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("order_quantity", sa.Integer(), nullable=False),
        sa.Column("reference_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["trade.paper_account.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["portfolio.portfolio_plan.id"]),
        sa.ForeignKeyConstraint(["check_id"], ["risk.risk_check.id"]),
        schema="trade",
    )
    op.create_index(
        "ix_paper_order_account_date",
        "paper_order",
        ["account_id", "trade_date"],
        schema="trade",
    )
    op.create_index(
        "ix_paper_order_check",
        "paper_order",
        ["check_id"],
        schema="trade",
    )
    op.create_table(
        "paper_fill",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["order_id"], ["trade.paper_order.id"]),
        sa.ForeignKeyConstraint(["account_id"], ["trade.paper_account.id"]),
        schema="trade",
    )
    op.create_index(
        "ix_paper_fill_order",
        "paper_fill",
        ["order_id"],
        schema="trade",
    )
    op.create_index(
        "ix_paper_fill_account_date",
        "paper_fill",
        ["account_id", "trade_date"],
        schema="trade",
    )
    op.create_table(
        "paper_position",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_cost", sa.Numeric(18, 6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["trade.paper_account.id"]),
        schema="trade",
    )
    op.create_index(
        "ix_paper_position_account_symbol",
        "paper_position",
        ["account_id", "symbol"],
        unique=True,
        schema="trade",
    )


def downgrade() -> None:
    op.drop_table("paper_position", schema="trade")
    op.drop_table("paper_fill", schema="trade")
    op.drop_table("paper_order", schema="trade")
    op.drop_table("paper_account", schema="trade")
