"""add factor cross section profiles

Revision ID: 20260717_0007
Revises: 20260717_0006
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0007"
down_revision: str | None = "20260717_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "factor_profile",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("factor_name", sa.String(length=100), nullable=False),
        sa.Column("factor_version", sa.String(length=100), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("universe_size", sa.Integer(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("coverage_rate", sa.Numeric(12, 10), nullable=False),
        sa.Column("missing_rate", sa.Numeric(12, 10), nullable=False),
        sa.Column("mean", sa.Numeric(24, 10), nullable=True),
        sa.Column("standard_deviation", sa.Numeric(24, 10), nullable=True),
        sa.Column("minimum", sa.Numeric(24, 10), nullable=True),
        sa.Column("maximum", sa.Numeric(24, 10), nullable=True),
        sa.Column("p01", sa.Numeric(24, 10), nullable=True),
        sa.Column("p99", sa.Numeric(24, 10), nullable=True),
        sa.Column("extreme_count", sa.Integer(), nullable=False),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("factor_name", "factor_version", "trade_date"),
        schema="factor",
    )
    op.create_index(
        "ix_factor_profile_factor_date",
        "factor_profile",
        ["factor_name", "factor_version", "trade_date"],
        schema="factor",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_factor_profile_factor_date",
        table_name="factor_profile",
        schema="factor",
    )
    op.drop_table("factor_profile", schema="factor")
