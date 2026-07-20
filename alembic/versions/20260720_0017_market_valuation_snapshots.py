"""add market valuation snapshots

Revision ID: 20260720_0017
Revises: 20260718_0016
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260720_0017"
down_revision: str | None = "20260718_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "valuation_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("total_market_cap", sa.Numeric(30, 6)),
        sa.Column("float_market_cap", sa.Numeric(30, 6)),
        sa.Column("total_shares", sa.Numeric(30, 6)),
        sa.Column("float_shares", sa.Numeric(30, 6)),
        sa.Column("pe_ttm", sa.Numeric(24, 10)),
        sa.Column("pb", sa.Numeric(24, 10)),
        sa.Column("ps_ttm", sa.Numeric(24, 10)),
        sa.Column("close", sa.Numeric(18, 4)),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("symbol", "trade_date", "source_name"),
        schema="market",
    )
    op.create_index(
        "ix_valuation_snapshot_symbol_date",
        "valuation_snapshot",
        ["symbol", "trade_date"],
        schema="market",
    )
    op.create_index(
        "ix_valuation_snapshot_date",
        "valuation_snapshot",
        ["trade_date"],
        schema="market",
    )


def downgrade() -> None:
    op.drop_table("valuation_snapshot", schema="market")
