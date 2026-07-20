"""add backtest persistence

Revision ID: 20260717_0008
Revises: 20260717_0007
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0008"
down_revision: str | None = "20260717_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS strategy")
    op.create_table(
        "backtest_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("strategy_name", sa.String(length=100), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column("data_version", sa.String(length=100), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_cash", sa.Numeric(24, 4), nullable=False),
        sa.Column("final_equity", sa.Numeric(24, 4), nullable=True),
        sa.Column("total_return", sa.Numeric(18, 10), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status in ('running', 'success', 'failed')",
            name="ck_backtest_run_status",
        ),
        sa.CheckConstraint("initial_cash > 0", name="ck_backtest_run_initial_cash"),
        sa.CheckConstraint("start_date <= end_date", name="ck_backtest_run_date_order"),
        schema="strategy",
    )
    op.create_index(
        "ix_backtest_run_strategy_started",
        "backtest_run",
        ["strategy_name", "started_at"],
        schema="strategy",
    )

    op.create_table(
        "backtest_trade",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("signal_date", sa.Date(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("side", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reference_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("fill_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("commission", sa.Numeric(18, 6), nullable=False),
        sa.Column("cash_after", sa.Numeric(24, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["strategy.backtest_run.id"]),
        sa.CheckConstraint("side in ('buy', 'sell')", name="ck_backtest_trade_side"),
        sa.CheckConstraint("quantity > 0", name="ck_backtest_trade_quantity"),
        schema="strategy",
    )
    op.create_index(
        "ix_backtest_trade_run_date",
        "backtest_trade",
        ["run_id", "trade_date"],
        schema="strategy",
    )

    op.create_table(
        "backtest_equity",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("cash", sa.Numeric(24, 4), nullable=False),
        sa.Column("market_value", sa.Numeric(24, 4), nullable=False),
        sa.Column("total_equity", sa.Numeric(24, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["strategy.backtest_run.id"]),
        sa.UniqueConstraint("run_id", "trade_date"),
        schema="strategy",
    )
    op.create_index(
        "ix_backtest_equity_run_date",
        "backtest_equity",
        ["run_id", "trade_date"],
        schema="strategy",
    )

    op.create_table(
        "backtest_position",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("market_value", sa.Numeric(24, 4), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["strategy.backtest_run.id"]),
        sa.UniqueConstraint("run_id", "trade_date", "symbol"),
        schema="strategy",
    )
    op.create_index(
        "ix_backtest_position_run_date",
        "backtest_position",
        ["run_id", "trade_date"],
        schema="strategy",
    )


def downgrade() -> None:
    op.drop_table("backtest_position", schema="strategy")
    op.drop_table("backtest_equity", schema="strategy")
    op.drop_table("backtest_trade", schema="strategy")
    op.drop_table("backtest_run", schema="strategy")
    op.execute("DROP SCHEMA IF EXISTS strategy")
