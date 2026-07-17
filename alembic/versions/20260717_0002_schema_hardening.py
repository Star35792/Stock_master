"""schema hardening for trusted data center

Revision ID: 20260717_0002
Revises: 20260716_0001
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260717_0002"
down_revision: str | None = "20260716_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        update raw.import_batch
        set finished_at = started_at
        where finished_at is not null
          and finished_at < started_at
        """
    )

    op.create_check_constraint(
        "ck_raw_import_batch_status",
        "import_batch",
        "status in ('running', 'success', 'failed', 'partial')",
        schema="raw",
    )
    op.create_check_constraint(
        "ck_raw_import_batch_counts_non_negative",
        "import_batch",
        "row_count >= 0 and accepted_count >= 0 and rejected_count >= 0",
        schema="raw",
    )
    op.create_check_constraint(
        "ck_raw_import_batch_counts_consistent",
        "import_batch",
        "accepted_count + rejected_count <= row_count",
        schema="raw",
    )
    op.create_check_constraint(
        "ck_raw_import_batch_time_order",
        "import_batch",
        "finished_at is null or finished_at >= started_at",
        schema="raw",
    )

    op.create_check_constraint(
        "ck_raw_daily_bar_adjust_type",
        "daily_bar",
        "adjust_type in ('none', 'qfq', 'hfq')",
        schema="raw",
    )
    op.create_check_constraint(
        "ck_raw_daily_bar_source_priority_non_negative",
        "daily_bar",
        "source_priority >= 0",
        schema="raw",
    )
    op.create_index(
        "ix_raw_daily_bar_symbol_adjust_date",
        "daily_bar",
        ["symbol", "adjust_type", "trade_date"],
        schema="raw",
    )
    op.create_index(
        "ix_raw_daily_bar_source_symbol_adjust_date",
        "daily_bar",
        ["source_name", "symbol", "adjust_type", "trade_date"],
        schema="raw",
    )

    op.create_check_constraint(
        "ck_security_type",
        "security",
        "security_type in ('stock', 'etf', 'index', 'fund', 'bond', 'unknown')",
        schema="market",
    )
    op.create_check_constraint(
        "ck_security_exchange",
        "security",
        "exchange in ('SH', 'SZ', 'BJ', 'HK', 'UNKNOWN')",
        schema="market",
    )
    op.create_check_constraint(
        "ck_trading_calendar_exchange",
        "trading_calendar",
        "exchange in ('SH', 'SZ', 'BJ', 'HK', 'UNKNOWN')",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_adjust_type",
        "daily_bar",
        "adjust_type in ('none', 'qfq', 'hfq')",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_quality_status_allowed",
        "daily_bar",
        "quality_status in ('trusted', 'suspect', 'rejected', 'manual_fixed')",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_quality_score_range",
        "daily_bar",
        "quality_score >= 0 and quality_score <= 100",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_source_count_positive",
        "daily_bar",
        "source_count >= 1",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_ohlc_positive",
        "daily_bar",
        "open > 0 and high > 0 and low > 0 and close > 0",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_ohlc_order",
        "daily_bar",
        "high >= open and high >= close and high >= low and "
        "low <= open and low <= close and low <= high",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_volume_non_negative",
        "daily_bar",
        "volume is null or volume >= 0",
        schema="market",
    )
    op.create_check_constraint(
        "ck_daily_bar_amount_non_negative",
        "daily_bar",
        "amount is null or amount >= 0",
        schema="market",
    )
    op.create_foreign_key(
        "fk_daily_bar_symbol_security",
        source_table="daily_bar",
        referent_table="security",
        local_cols=["symbol"],
        remote_cols=["symbol"],
        source_schema="market",
        referent_schema="market",
    )
    op.create_index(
        "ix_daily_bar_status_symbol_adjust_date",
        "daily_bar",
        ["quality_status", "symbol", "adjust_type", "trade_date"],
        schema="market",
    )
    op.create_index(
        "ix_daily_bar_trusted_symbol_adjust_date",
        "daily_bar",
        ["symbol", "adjust_type", "trade_date"],
        schema="market",
        postgresql_where=sa.text("quality_status in ('trusted', 'manual_fixed')"),
    )
    op.create_check_constraint(
        "ck_daily_bar_compare_distinct_raw",
        "daily_bar_source_compare",
        "raw_id_a <> raw_id_b",
        schema="market",
    )
    op.create_index(
        "ix_manual_correction_symbol_date_field",
        "manual_correction",
        ["symbol", "trade_date", "adjust_type", "field_name"],
        schema="market",
    )

    op.create_check_constraint(
        "ck_quality_issue_severity",
        "data_quality_issue",
        "severity in ('info', 'warning', 'error', 'critical')",
        schema="system",
    )
    op.create_check_constraint(
        "ck_quality_issue_status_allowed",
        "data_quality_issue",
        "status in ('open', 'ignored', 'fixed', 'false_positive')",
        schema="system",
    )
    op.create_index(
        "ix_quality_issue_symbol_status_date",
        "data_quality_issue",
        ["symbol", "status", "trade_date"],
        schema="system",
    )
    op.create_index(
        "ix_quality_issue_status_severity_created",
        "data_quality_issue",
        ["status", "severity", "created_at"],
        schema="system",
    )
    op.create_check_constraint(
        "ck_job_log_type",
        "job_log",
        "job_type in ('import', 'normalize', 'validate', 'reconcile', 'repair', 'schedule')",
        schema="system",
    )
    op.create_check_constraint(
        "ck_job_log_status",
        "job_log",
        "status in ('running', 'success', 'failed', 'partial')",
        schema="system",
    )
    op.create_check_constraint(
        "ck_job_log_counts_non_negative",
        "job_log",
        "rows_read >= 0 and rows_inserted >= 0 and rows_updated >= 0 and rows_failed >= 0",
        schema="system",
    )
    op.create_check_constraint(
        "ck_job_log_time_order",
        "job_log",
        "finished_at is null or finished_at >= started_at",
        schema="system",
    )
    op.create_index(
        "ix_job_log_type_status_started",
        "job_log",
        ["job_type", "status", "started_at"],
        schema="system",
    )
    op.create_index("ix_job_log_batch_id", "job_log", ["batch_id"], schema="system")
    op.create_check_constraint(
        "ck_data_source_type",
        "data_source",
        "source_type in ('csv', 'api', 'akshare', 'qmt', 'manual', 'vendor')",
        schema="system",
    )
    op.create_check_constraint(
        "ck_data_source_priority_non_negative",
        "data_source",
        "priority >= 0",
        schema="system",
    )
    op.create_index(
        "ix_data_source_active_priority",
        "data_source",
        ["is_active", "priority"],
        schema="system",
    )


def downgrade() -> None:
    op.drop_index("ix_data_source_active_priority", table_name="data_source", schema="system")
    op.drop_constraint(
        "ck_data_source_priority_non_negative", "data_source", schema="system", type_="check"
    )
    op.drop_constraint("ck_data_source_type", "data_source", schema="system", type_="check")

    op.drop_index("ix_job_log_batch_id", table_name="job_log", schema="system")
    op.drop_index("ix_job_log_type_status_started", table_name="job_log", schema="system")
    op.drop_constraint("ck_job_log_time_order", "job_log", schema="system", type_="check")
    op.drop_constraint("ck_job_log_counts_non_negative", "job_log", schema="system", type_="check")
    op.drop_constraint("ck_job_log_status", "job_log", schema="system", type_="check")
    op.drop_constraint("ck_job_log_type", "job_log", schema="system", type_="check")

    op.drop_index(
        "ix_quality_issue_status_severity_created", table_name="data_quality_issue", schema="system"
    )
    op.drop_index(
        "ix_quality_issue_symbol_status_date", table_name="data_quality_issue", schema="system"
    )
    op.drop_constraint(
        "ck_quality_issue_status_allowed", "data_quality_issue", schema="system", type_="check"
    )
    op.drop_constraint(
        "ck_quality_issue_severity", "data_quality_issue", schema="system", type_="check"
    )

    op.drop_index(
        "ix_manual_correction_symbol_date_field", table_name="manual_correction", schema="market"
    )
    op.drop_constraint(
        "ck_daily_bar_compare_distinct_raw",
        "daily_bar_source_compare",
        schema="market",
        type_="check",
    )
    op.drop_index(
        "ix_daily_bar_trusted_symbol_adjust_date", table_name="daily_bar", schema="market"
    )
    op.drop_index("ix_daily_bar_status_symbol_adjust_date", table_name="daily_bar", schema="market")
    op.drop_constraint(
        "fk_daily_bar_symbol_security", "daily_bar", schema="market", type_="foreignkey"
    )
    op.drop_constraint(
        "ck_daily_bar_amount_non_negative", "daily_bar", schema="market", type_="check"
    )
    op.drop_constraint(
        "ck_daily_bar_volume_non_negative", "daily_bar", schema="market", type_="check"
    )
    op.drop_constraint("ck_daily_bar_ohlc_order", "daily_bar", schema="market", type_="check")
    op.drop_constraint("ck_daily_bar_ohlc_positive", "daily_bar", schema="market", type_="check")
    op.drop_constraint(
        "ck_daily_bar_source_count_positive", "daily_bar", schema="market", type_="check"
    )
    op.drop_constraint(
        "ck_daily_bar_quality_score_range", "daily_bar", schema="market", type_="check"
    )
    op.drop_constraint(
        "ck_daily_bar_quality_status_allowed", "daily_bar", schema="market", type_="check"
    )
    op.drop_constraint("ck_daily_bar_adjust_type", "daily_bar", schema="market", type_="check")
    op.drop_constraint(
        "ck_trading_calendar_exchange", "trading_calendar", schema="market", type_="check"
    )
    op.drop_constraint("ck_security_exchange", "security", schema="market", type_="check")
    op.drop_constraint("ck_security_type", "security", schema="market", type_="check")

    op.drop_index(
        "ix_raw_daily_bar_source_symbol_adjust_date", table_name="daily_bar", schema="raw"
    )
    op.drop_index("ix_raw_daily_bar_symbol_adjust_date", table_name="daily_bar", schema="raw")
    op.drop_constraint(
        "ck_raw_daily_bar_source_priority_non_negative", "daily_bar", schema="raw", type_="check"
    )
    op.drop_constraint("ck_raw_daily_bar_adjust_type", "daily_bar", schema="raw", type_="check")
    op.drop_constraint(
        "ck_raw_import_batch_time_order", "import_batch", schema="raw", type_="check"
    )
    op.drop_constraint(
        "ck_raw_import_batch_counts_consistent", "import_batch", schema="raw", type_="check"
    )
    op.drop_constraint(
        "ck_raw_import_batch_counts_non_negative", "import_batch", schema="raw", type_="check"
    )
    op.drop_constraint("ck_raw_import_batch_status", "import_batch", schema="raw", type_="check")
