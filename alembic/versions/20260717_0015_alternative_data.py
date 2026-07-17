"""add alternative data snapshot tables

Revision ID: 20260717_0015
Revises: 20260717_0014
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0015"
down_revision: str | None = "20260717_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vendor_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["raw.import_batch.id"]),
        schema="raw",
    )
    op.create_index(
        "ix_raw_vendor_snapshot_dataset_date",
        "vendor_snapshot",
        ["dataset", "snapshot_date"],
        schema="raw",
    )
    op.create_index(
        "ix_raw_vendor_snapshot_batch",
        "vendor_snapshot",
        ["batch_id"],
        schema="raw",
    )
    op.create_table(
        "alternative_data_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("item_key", sa.String(length=150), nullable=False),
        sa.Column("symbol", sa.String(length=20)),
        sa.Column("rank", sa.Integer()),
        sa.Column("value", sa.Numeric(30, 10)),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index(
        "uq_alternative_snapshot_key",
        "alternative_data_snapshot",
        ["dataset", "snapshot_date", "item_key"],
        unique=True,
        schema="market",
    )
    op.create_index(
        "ix_alternative_snapshot_symbol_date",
        "alternative_data_snapshot",
        ["symbol", "snapshot_date"],
        schema="market",
    )
    op.create_table(
        "index_constituent_snapshot",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("index_symbol", sa.String(length=20), nullable=False),
        sa.Column("member_symbol", sa.String(length=20), nullable=False),
        sa.Column("member_name", sa.String(length=100), nullable=False),
        sa.Column("weight", sa.Numeric(20, 10)),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="market",
    )
    op.create_index(
        "uq_index_constituent_snapshot_key",
        "index_constituent_snapshot",
        ["snapshot_date", "index_symbol", "member_symbol"],
        unique=True,
        schema="market",
    )
    op.create_index(
        "ix_index_constituent_member_date",
        "index_constituent_snapshot",
        ["member_symbol", "snapshot_date"],
        schema="market",
    )


def downgrade() -> None:
    op.drop_table("index_constituent_snapshot", schema="market")
    op.drop_table("alternative_data_snapshot", schema="market")
    op.drop_table("vendor_snapshot", schema="raw")
