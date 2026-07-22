"""add portfolio plan draft audit fields

Revision ID: 20260721_0019
Revises: 20260720_0018
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260721_0019"
down_revision: str | None = "20260720_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "portfolio_plan",
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        schema="portfolio",
    )
    op.add_column(
        "portfolio_plan",
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        schema="portfolio",
    )
    op.add_column(
        "portfolio_plan",
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="portfolio",
    )
    op.create_index(
        "uq_portfolio_plan_idempotency_key",
        "portfolio_plan",
        ["idempotency_key"],
        unique=True,
        schema="portfolio",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_portfolio_plan_idempotency_key",
        table_name="portfolio_plan",
        schema="portfolio",
    )
    op.drop_column("portfolio_plan", "input_snapshot", schema="portfolio")
    op.drop_column("portfolio_plan", "request_hash", schema="portfolio")
    op.drop_column("portfolio_plan", "idempotency_key", schema="portfolio")
