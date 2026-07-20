"""add backtest performance reports

Revision ID: 20260717_0009
Revises: 20260717_0008
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0009"
down_revision: str | None = "20260717_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_performance",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("total_return", sa.Numeric(18, 10), nullable=False),
        sa.Column("annualized_return", sa.Numeric(18, 10), nullable=False),
        sa.Column("maximum_drawdown", sa.Numeric(18, 10), nullable=False),
        sa.Column("annualized_volatility", sa.Numeric(18, 10), nullable=False),
        sa.Column("sharpe_ratio", sa.Numeric(18, 10), nullable=True),
        sa.Column("calmar_ratio", sa.Numeric(18, 10), nullable=True),
        sa.Column("daily_win_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("profit_loss_ratio", sa.Numeric(18, 10), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(18, 10), nullable=False),
        sa.Column("total_commission", sa.Numeric(24, 6), nullable=False),
        sa.Column(
            "symbol_contributions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "industry_exposure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "style_exposure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["strategy.backtest_run.id"]),
        sa.UniqueConstraint("run_id"),
        schema="strategy",
    )


def downgrade() -> None:
    op.drop_table("backtest_performance", schema="strategy")
