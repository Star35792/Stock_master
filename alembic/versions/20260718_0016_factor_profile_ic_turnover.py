"""add factor profile ic and turnover

Revision ID: 20260718_0016
Revises: 20260717_0015
Create Date: 2026-07-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260718_0016"
down_revision: str | None = "20260717_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "factor_profile",
        sa.Column("ic_1d", sa.Numeric(24, 10), nullable=True),
        schema="factor",
    )
    op.add_column(
        "factor_profile",
        sa.Column("rank_ic_1d", sa.Numeric(24, 10), nullable=True),
        schema="factor",
    )
    op.add_column(
        "factor_profile",
        sa.Column("top_quantile_turnover", sa.Numeric(12, 10), nullable=True),
        schema="factor",
    )


def downgrade() -> None:
    op.drop_column("factor_profile", "top_quantile_turnover", schema="factor")
    op.drop_column("factor_profile", "rank_ic_1d", schema="factor")
    op.drop_column("factor_profile", "ic_1d", schema="factor")
