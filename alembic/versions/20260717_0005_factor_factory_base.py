"""add factor factory base tables

Revision ID: 20260717_0005
Revises: 20260717_0004
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0005"
down_revision: str | None = "20260717_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS factor")

    op.create_table(
        "factor_definition",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column(
            "inputs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("window_days", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column(
            "dependencies",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "parameters_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("name", "version"),
        sa.CheckConstraint(
            "frequency in ('daily', 'weekly', 'monthly')",
            name="ck_factor_definition_frequency",
        ),
        sa.CheckConstraint(
            "window_days is null or window_days > 0",
            name="ck_factor_definition_window_positive",
        ),
        schema="factor",
    )
    op.create_index(
        "ix_factor_definition_name_version",
        "factor_definition",
        ["name", "version"],
        schema="factor",
    )
    op.create_index(
        "ix_factor_definition_active_frequency",
        "factor_definition",
        ["is_active", "frequency"],
        schema="factor",
    )

    op.create_table(
        "factor_run",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("definition_id", sa.BigInteger(), nullable=False),
        sa.Column("factor_name", sa.String(length=100), nullable=False),
        sa.Column("factor_version", sa.String(length=50), nullable=False),
        sa.Column(
            "parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("code_version", sa.String(length=100), nullable=False),
        sa.Column("input_start_date", sa.Date(), nullable=True),
        sa.Column("input_end_date", sa.Date(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'running'"),
            nullable=False,
        ),
        sa.Column("rows_written", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["definition_id"], ["factor.factor_definition.id"]),
        sa.CheckConstraint(
            "status in ('running', 'success', 'failed', 'partial')",
            name="ck_factor_run_status",
        ),
        sa.CheckConstraint("rows_written >= 0", name="ck_factor_run_rows_written"),
        sa.CheckConstraint(
            "input_start_date is null or input_end_date is null or input_start_date <= input_end_date",
            name="ck_factor_run_input_date_order",
        ),
        sa.CheckConstraint(
            "finished_at is null or finished_at >= started_at",
            name="ck_factor_run_time_order",
        ),
        schema="factor",
    )
    op.create_index(
        "ix_factor_run_factor_status_started",
        "factor_run",
        ["factor_name", "status", "started_at"],
        schema="factor",
    )
    op.create_index(
        "ix_factor_run_definition",
        "factor_run",
        ["definition_id"],
        schema="factor",
    )

    op.create_table(
        "factor_value",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("factor_name", sa.String(length=100), nullable=False),
        sa.Column("factor_version", sa.String(length=50), nullable=False),
        sa.Column("value", sa.Numeric(24, 10), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["factor.factor_run.id"]),
        sa.ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        sa.UniqueConstraint("symbol", "trade_date", "factor_name", "factor_version"),
        schema="factor",
    )
    op.create_index(
        "ix_factor_value_factor_date",
        "factor_value",
        ["factor_name", "factor_version", "trade_date"],
        schema="factor",
    )
    op.create_index(
        "ix_factor_value_symbol_factor_date",
        "factor_value",
        ["symbol", "factor_name", "trade_date"],
        schema="factor",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_factor_value_symbol_factor_date", table_name="factor_value", schema="factor"
    )
    op.drop_index("ix_factor_value_factor_date", table_name="factor_value", schema="factor")
    op.drop_table("factor_value", schema="factor")

    op.drop_index("ix_factor_run_definition", table_name="factor_run", schema="factor")
    op.drop_index(
        "ix_factor_run_factor_status_started", table_name="factor_run", schema="factor"
    )
    op.drop_table("factor_run", schema="factor")

    op.drop_index(
        "ix_factor_definition_active_frequency",
        table_name="factor_definition",
        schema="factor",
    )
    op.drop_index(
        "ix_factor_definition_name_version",
        table_name="factor_definition",
        schema="factor",
    )
    op.drop_table("factor_definition", schema="factor")
    op.execute("DROP SCHEMA IF EXISTS factor")
