"""add portfolio construction plans

Revision ID: 20260717_0010
Revises: 20260717_0009
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0010"
down_revision: str | None = "20260717_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS portfolio")
    op.create_table(
        "portfolio_plan",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("total_equity", sa.Numeric(24, 4), nullable=False),
        sa.Column("cash_before", sa.Numeric(24, 4), nullable=False),
        sa.Column("target_cash", sa.Numeric(24, 4), nullable=False),
        sa.Column("cash_after", sa.Numeric(24, 4), nullable=False),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="portfolio",
    )
    op.create_index(
        "ix_portfolio_plan_as_of_date",
        "portfolio_plan",
        ["as_of_date"],
        schema="portfolio",
    )
    op.create_table(
        "portfolio_target",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("security_type", sa.String(length=20), nullable=False),
        sa.Column("industry", sa.String(length=100), nullable=False),
        sa.Column("requested_weight", sa.Numeric(18, 10), nullable=False),
        sa.Column("constrained_weight", sa.Numeric(18, 10), nullable=False),
        sa.Column("final_weight", sa.Numeric(18, 10), nullable=False),
        sa.Column("current_quantity", sa.Integer(), nullable=False),
        sa.Column("target_quantity", sa.Integer(), nullable=False),
        sa.Column("order_quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(18, 6), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["plan_id"], ["portfolio.portfolio_plan.id"]),
        sa.UniqueConstraint("plan_id", "symbol"),
        schema="portfolio",
    )
    op.create_index(
        "ix_portfolio_target_plan",
        "portfolio_target",
        ["plan_id"],
        schema="portfolio",
    )


def downgrade() -> None:
    op.drop_table("portfolio_target", schema="portfolio")
    op.drop_table("portfolio_plan", schema="portfolio")
    op.execute("DROP SCHEMA IF EXISTS portfolio")
