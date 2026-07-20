"""add raw corporate actions

Revision ID: 20260717_0013
Revises: 20260717_0012
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0013"
down_revision: str | None = "20260717_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corporate_action",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("dividend_per_share", sa.Numeric(18, 6)),
        sa.Column("per_share_bonus", sa.Numeric(18, 8)),
        sa.Column("allotment_ratio", sa.Numeric(18, 8)),
        sa.Column("allotment_price", sa.Numeric(18, 6)),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["raw.import_batch.id"]),
        schema="raw",
    )
    op.create_index(
        "ix_raw_corporate_action_symbol_date",
        "corporate_action",
        ["symbol", "ex_date"],
        schema="raw",
    )
    op.create_index(
        "ix_raw_corporate_action_batch",
        "corporate_action",
        ["batch_id"],
        schema="raw",
    )
    op.create_index(
        "ix_raw_corporate_action_hash",
        "corporate_action",
        ["row_hash"],
        schema="raw",
    )


def downgrade() -> None:
    op.drop_table("corporate_action", schema="raw")
