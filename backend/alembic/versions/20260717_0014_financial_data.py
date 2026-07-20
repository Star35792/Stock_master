"""add financial data tables

Revision ID: 20260717_0014
Revises: 20260717_0013
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0014"
down_revision: str | None = "20260717_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS fundamental")
    op.create_table(
        "financial_payload",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("dataset", sa.String(length=20), nullable=False),
        sa.Column("report_key", sa.String(length=20), nullable=False),
        sa.Column("report_date", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["raw.import_batch.id"]),
        sa.CheckConstraint(
            "dataset in ('income', 'balance', 'cash_flow', 'indicators')",
            name="ck_financial_payload_dataset_allowed",
        ),
        schema="raw",
    )
    op.create_index(
        "ix_raw_financial_symbol_dataset",
        "financial_payload",
        ["symbol", "dataset"],
        schema="raw",
    )
    op.create_index(
        "ix_raw_financial_batch",
        "financial_payload",
        ["batch_id"],
        schema="raw",
    )

    op.create_table(
        "financial_statement",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("statement_type", sa.String(length=20), nullable=False),
        sa.Column("period", sa.String(length=20), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_period", sa.String(length=10), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint(
            "statement_type in ('income', 'balance', 'cash_flow')",
            name="ck_financial_statement_statement_type_allowed",
        ),
        schema="fundamental",
    )
    op.create_index(
        "uq_financial_statement_key",
        "financial_statement",
        ["symbol", "statement_type", "period", "fiscal_year", "fiscal_period"],
        unique=True,
        schema="fundamental",
    )
    op.create_index(
        "ix_financial_statement_symbol_period_end",
        "financial_statement",
        ["symbol", "period_end"],
        schema="fundamental",
    )

    op.create_table(
        "financial_indicator",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("report", sa.String(length=10), nullable=False),
        sa.Column("ability", sa.String(length=30), nullable=False),
        sa.Column("indicator_name", sa.String(length=100), nullable=False),
        sa.Column("value", sa.Numeric(30, 10)),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="fundamental",
    )
    op.create_index(
        "uq_financial_indicator_key",
        "financial_indicator",
        ["symbol", "report", "ability", "indicator_name"],
        unique=True,
        schema="fundamental",
    )
    op.create_index(
        "ix_financial_indicator_name_report",
        "financial_indicator",
        ["indicator_name", "report"],
        schema="fundamental",
    )


def downgrade() -> None:
    op.drop_table("financial_indicator", schema="fundamental")
    op.drop_table("financial_statement", schema="fundamental")
    op.drop_table("financial_payload", schema="raw")
