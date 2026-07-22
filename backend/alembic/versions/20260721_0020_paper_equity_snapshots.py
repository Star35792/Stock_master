"""add immutable paper account equity snapshots

Revision ID: 20260721_0020
Revises: 20260721_0019
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260721_0020"
down_revision: str | None = "20260721_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_account_equity_snapshot",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("valuation_date", sa.Date(), nullable=False),
        sa.Column("cash_balance", sa.Numeric(24, 4), nullable=False),
        sa.Column("positions_market_value", sa.Numeric(24, 4), nullable=False),
        sa.Column("total_equity", sa.Numeric(24, 4), nullable=False),
        sa.Column("high_watermark", sa.Numeric(24, 4), nullable=False),
        sa.Column("drawdown", sa.Numeric(18, 10), nullable=False),
        sa.Column("previous_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("monitoring_start_date", sa.Date(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("history_status", sa.String(length=20), nullable=False),
        sa.Column("account_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("market_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("valuation_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "valuation_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
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
            "history_status in ('bootstrap', 'continuous', 'gap')",
            name="ck_paper_equity_history_status",
        ),
        sa.CheckConstraint(
            "drawdown >= -1 and drawdown <= 0",
            name="ck_paper_equity_drawdown_range",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["trade.paper_account.id"],
        ),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"],
            ["trade.paper_account_equity_snapshot.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "valuation_date",
            name="uq_paper_equity_snapshot_account_date",
        ),
        schema="trade",
    )
    op.create_index(
        "ix_paper_equity_snapshot_account_date",
        "paper_account_equity_snapshot",
        ["account_id", "valuation_date"],
        schema="trade",
    )
    op.create_index(
        "uq_paper_equity_snapshot_idempotency_key",
        "paper_account_equity_snapshot",
        ["idempotency_key"],
        unique=True,
        schema="trade",
    )
    op.create_table(
        "paper_account_equity_component",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("average_cost", sa.Numeric(18, 6), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("market_value", sa.Numeric(24, 4), nullable=False),
        sa.Column("daily_bar_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("quality_status", sa.String(length=20), nullable=False),
        sa.Column("bar_trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("is_suspect", sa.Boolean(), nullable=False),
        sa.Column("bar_updated_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(["daily_bar_id"], ["market.daily_bar.id"]),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["trade.paper_account_equity_snapshot.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("snapshot_id", "symbol"),
        schema="trade",
    )
    op.create_index(
        "ix_paper_equity_component_snapshot",
        "paper_account_equity_component",
        ["snapshot_id"],
        schema="trade",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_equity_component_snapshot",
        table_name="paper_account_equity_component",
        schema="trade",
    )
    op.drop_table("paper_account_equity_component", schema="trade")
    op.drop_index(
        "uq_paper_equity_snapshot_idempotency_key",
        table_name="paper_account_equity_snapshot",
        schema="trade",
    )
    op.drop_index(
        "ix_paper_equity_snapshot_account_date",
        table_name="paper_account_equity_snapshot",
        schema="trade",
    )
    op.drop_table("paper_account_equity_snapshot", schema="trade")
