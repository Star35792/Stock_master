"""initial trusted data center schema

Revision ID: 20260716_0001
Revises:
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260716_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS raw")
    op.execute("CREATE SCHEMA IF NOT EXISTS market")
    op.execute("CREATE SCHEMA IF NOT EXISTS system")

    op.create_table(
        "import_batch",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_no", sa.String(length=64), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("request_params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column("row_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("accepted_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rejected_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("batch_no"),
        schema="raw",
    )
    op.create_index(
        "ix_import_batch_source_dataset", "import_batch", ["source_name", "dataset"], schema="raw"
    )
    op.create_index("ix_import_batch_status", "import_batch", ["status"], schema="raw")
    op.create_index("ix_import_batch_started_at", "import_batch", ["started_at"], schema="raw")

    op.create_table(
        "daily_bar",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_priority", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("raw_symbol", sa.String(length=30), nullable=True),
        sa.Column("security_type", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=True),
        sa.Column("high", sa.Numeric(18, 4), nullable=True),
        sa.Column("low", sa.Numeric(18, 4), nullable=True),
        sa.Column("close", sa.Numeric(18, 4), nullable=True),
        sa.Column("pre_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("change_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("pct_change", sa.Numeric(12, 6), nullable=True),
        sa.Column("volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("amount", sa.Numeric(24, 4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("amplitude", sa.Numeric(12, 6), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("row_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["raw.import_batch.id"]),
        schema="raw",
    )
    op.create_index(
        "ix_raw_daily_bar_symbol_date", "daily_bar", ["symbol", "trade_date"], schema="raw"
    )
    op.create_index(
        "ix_raw_daily_bar_source_date", "daily_bar", ["source_name", "trade_date"], schema="raw"
    )
    op.create_index("ix_raw_daily_bar_batch_id", "daily_bar", ["batch_id"], schema="raw")
    op.create_index("ix_raw_daily_bar_row_hash", "daily_bar", ["row_hash"], schema="raw")

    op.create_table(
        "security",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("raw_symbol", sa.String(length=30), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("security_type", sa.String(length=20), nullable=False),
        sa.Column("exchange", sa.String(length=10), nullable=False),
        sa.Column(
            "currency", sa.String(length=10), server_default=sa.text("'CNY'"), nullable=False
        ),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("industry", sa.String(length=100), nullable=True),
        sa.Column("area", sa.String(length=100), nullable=True),
        sa.Column("board", sa.String(length=50), nullable=True),
        sa.Column("source_name", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("symbol"),
        schema="market",
    )
    op.create_index("ix_security_type", "security", ["security_type"], schema="market")
    op.create_index("ix_security_exchange", "security", ["exchange"], schema="market")
    op.create_index("ix_security_name", "security", ["name"], schema="market")

    op.create_table(
        "trading_calendar",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("exchange", sa.String(length=10), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("previous_trade_date", sa.Date(), nullable=True),
        sa.Column("next_trade_date", sa.Date(), nullable=True),
        sa.Column("is_half_day", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("exchange", "trade_date"),
        schema="market",
    )
    op.create_index(
        "ix_trading_calendar_trade_date", "trading_calendar", ["trade_date"], schema="market"
    )
    op.create_index(
        "ix_trading_calendar_exchange_open",
        "trading_calendar",
        ["exchange", "is_open"],
        schema="market",
    )

    op.create_table(
        "daily_bar",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("security_type", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("pre_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("change_amount", sa.Numeric(18, 4), nullable=True),
        sa.Column("pct_change", sa.Numeric(12, 6), nullable=True),
        sa.Column("volume", sa.Numeric(24, 4), nullable=True),
        sa.Column("amount", sa.Numeric(24, 4), nullable=True),
        sa.Column("turnover_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("amplitude", sa.Numeric(12, 6), nullable=True),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("source_count", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("quality_score", sa.Numeric(5, 2), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "quality_status",
            sa.String(length=20),
            server_default=sa.text("'suspect'"),
            nullable=False,
        ),
        sa.Column("is_suspect", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("selected_raw_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["selected_raw_id"], ["raw.daily_bar.id"]),
        sa.UniqueConstraint("symbol", "trade_date", "adjust_type"),
        schema="market",
    )
    op.create_index(
        "ix_daily_bar_symbol_adjust_date",
        "daily_bar",
        ["symbol", "adjust_type", "trade_date"],
        schema="market",
    )
    op.create_index(
        "ix_daily_bar_trade_date_type",
        "daily_bar",
        ["trade_date", "security_type"],
        schema="market",
    )
    op.create_index("ix_daily_bar_quality_status", "daily_bar", ["quality_status"], schema="market")

    op.create_table(
        "daily_bar_source_compare",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("source_a", sa.String(length=50), nullable=False),
        sa.Column("source_b", sa.String(length=50), nullable=False),
        sa.Column("raw_id_a", sa.BigInteger(), nullable=False),
        sa.Column("raw_id_b", sa.BigInteger(), nullable=False),
        sa.Column("close_diff", sa.Numeric(18, 6), nullable=True),
        sa.Column("close_diff_pct", sa.Numeric(12, 6), nullable=True),
        sa.Column("volume_diff_pct", sa.Numeric(12, 6), nullable=True),
        sa.Column("amount_diff_pct", sa.Numeric(12, 6), nullable=True),
        sa.Column("is_conflict", sa.Boolean(), nullable=False),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["raw_id_a"], ["raw.daily_bar.id"]),
        sa.ForeignKeyConstraint(["raw_id_b"], ["raw.daily_bar.id"]),
        schema="market",
    )
    op.create_index(
        "ix_daily_bar_compare_symbol_date",
        "daily_bar_source_compare",
        ["symbol", "trade_date", "adjust_type"],
        schema="market",
    )
    op.create_index(
        "ix_daily_bar_compare_conflict",
        "daily_bar_source_compare",
        ["is_conflict"],
        schema="market",
    )

    op.create_table(
        "manual_correction",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("field_name", sa.String(length=50), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_by", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        schema="market",
    )

    op.create_table(
        "data_source",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("source_name"),
        schema="system",
    )

    op.create_table(
        "data_quality_issue",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("dataset", sa.String(length=50), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=True),
        sa.Column("trade_date", sa.Date(), nullable=True),
        sa.Column("adjust_type", sa.String(length=10), nullable=True),
        sa.Column("source_name", sa.String(length=50), nullable=True),
        sa.Column("raw_id", sa.BigInteger(), nullable=True),
        sa.Column("issue_type", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actual_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=20), server_default=sa.text("'open'"), nullable=False),
        sa.Column("resolved_by", sa.String(length=50), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["raw_id"], ["raw.daily_bar.id"]),
        schema="system",
    )
    op.create_index("ix_quality_issue_status", "data_quality_issue", ["status"], schema="system")
    op.create_index(
        "ix_quality_issue_symbol_date",
        "data_quality_issue",
        ["symbol", "trade_date"],
        schema="system",
    )
    op.create_index("ix_quality_issue_type", "data_quality_issue", ["issue_type"], schema="system")
    op.create_index(
        "ix_quality_issue_severity", "data_quality_issue", ["severity"], schema="system"
    )

    op.create_table(
        "job_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("job_name", sa.String(length=100), nullable=False),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("batch_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column("rows_read", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_updated", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["raw.import_batch.id"]),
        schema="system",
    )


def downgrade() -> None:
    op.drop_table("job_log", schema="system")

    op.drop_index("ix_quality_issue_severity", table_name="data_quality_issue", schema="system")
    op.drop_index("ix_quality_issue_type", table_name="data_quality_issue", schema="system")
    op.drop_index("ix_quality_issue_symbol_date", table_name="data_quality_issue", schema="system")
    op.drop_index("ix_quality_issue_status", table_name="data_quality_issue", schema="system")
    op.drop_table("data_quality_issue", schema="system")
    op.drop_table("data_source", schema="system")

    op.drop_table("manual_correction", schema="market")
    op.drop_index(
        "ix_daily_bar_compare_conflict", table_name="daily_bar_source_compare", schema="market"
    )
    op.drop_index(
        "ix_daily_bar_compare_symbol_date", table_name="daily_bar_source_compare", schema="market"
    )
    op.drop_table("daily_bar_source_compare", schema="market")
    op.drop_index("ix_daily_bar_quality_status", table_name="daily_bar", schema="market")
    op.drop_index("ix_daily_bar_trade_date_type", table_name="daily_bar", schema="market")
    op.drop_index("ix_daily_bar_symbol_adjust_date", table_name="daily_bar", schema="market")
    op.drop_table("daily_bar", schema="market")
    op.drop_index(
        "ix_trading_calendar_exchange_open", table_name="trading_calendar", schema="market"
    )
    op.drop_index("ix_trading_calendar_trade_date", table_name="trading_calendar", schema="market")
    op.drop_table("trading_calendar", schema="market")
    op.drop_index("ix_security_name", table_name="security", schema="market")
    op.drop_index("ix_security_exchange", table_name="security", schema="market")
    op.drop_index("ix_security_type", table_name="security", schema="market")
    op.drop_table("security", schema="market")

    op.drop_index("ix_raw_daily_bar_row_hash", table_name="daily_bar", schema="raw")
    op.drop_index("ix_raw_daily_bar_batch_id", table_name="daily_bar", schema="raw")
    op.drop_index("ix_raw_daily_bar_source_date", table_name="daily_bar", schema="raw")
    op.drop_index("ix_raw_daily_bar_symbol_date", table_name="daily_bar", schema="raw")
    op.drop_table("daily_bar", schema="raw")
    op.drop_index("ix_import_batch_started_at", table_name="import_batch", schema="raw")
    op.drop_index("ix_import_batch_status", table_name="import_batch", schema="raw")
    op.drop_index("ix_import_batch_source_dataset", table_name="import_batch", schema="raw")
    op.drop_table("import_batch", schema="raw")

    op.execute("DROP SCHEMA IF EXISTS system")
    op.execute("DROP SCHEMA IF EXISTS market")
    op.execute("DROP SCHEMA IF EXISTS raw")
