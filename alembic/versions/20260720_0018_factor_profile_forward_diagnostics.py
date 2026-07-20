"""add factor profile forward diagnostics

Revision ID: 20260720_0018
Revises: 20260720_0017
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260720_0018"
down_revision: str | None = "20260720_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("factor_profile", sa.Column("ic_5d", sa.Numeric(24, 10)), schema="factor")
    op.add_column("factor_profile", sa.Column("rank_ic_5d", sa.Numeric(24, 10)), schema="factor")
    op.add_column("factor_profile", sa.Column("ic_20d", sa.Numeric(24, 10)), schema="factor")
    op.add_column("factor_profile", sa.Column("rank_ic_20d", sa.Numeric(24, 10)), schema="factor")
    op.add_column(
        "factor_profile",
        sa.Column("top_quantile_forward_return_1d", sa.Numeric(24, 10)),
        schema="factor",
    )
    op.add_column(
        "factor_profile",
        sa.Column("bottom_quantile_forward_return_1d", sa.Numeric(24, 10)),
        schema="factor",
    )
    op.add_column(
        "factor_profile",
        sa.Column("long_short_forward_return_1d", sa.Numeric(24, 10)),
        schema="factor",
    )
    op.add_column(
        "factor_profile",
        sa.Column("quantile_monotonicity_1d", sa.Numeric(24, 10)),
        schema="factor",
    )


def downgrade() -> None:
    op.drop_column("factor_profile", "quantile_monotonicity_1d", schema="factor")
    op.drop_column("factor_profile", "long_short_forward_return_1d", schema="factor")
    op.drop_column("factor_profile", "bottom_quantile_forward_return_1d", schema="factor")
    op.drop_column("factor_profile", "top_quantile_forward_return_1d", schema="factor")
    op.drop_column("factor_profile", "rank_ic_20d", schema="factor")
    op.drop_column("factor_profile", "ic_20d", schema="factor")
    op.drop_column("factor_profile", "rank_ic_5d", schema="factor")
    op.drop_column("factor_profile", "ic_5d", schema="factor")
