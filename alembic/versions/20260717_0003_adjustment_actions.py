"""add adjustment factors and corporate actions

Revision ID: 20260717_0003
Revises: 20260717_0002
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0003"
down_revision: str | None = "20260717_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corporate_action",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("cash_dividend", sa.Numeric(18, 6), nullable=True),
        sa.Column("bonus_share_ratio", sa.Numeric(18, 8), nullable=True),
        sa.Column("transfer_share_ratio", sa.Numeric(18, 8), nullable=True),
        sa.Column("rights_issue_ratio", sa.Numeric(18, 8), nullable=True),
        sa.Column("rights_issue_price", sa.Numeric(18, 6), nullable=True),
        sa.Column("split_ratio", sa.Numeric(18, 8), nullable=True),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("source_event_id", sa.String(length=100), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        sa.UniqueConstraint("symbol", "ex_date", "action_type", "source_name"),
        sa.CheckConstraint(
            "action_type in ('cash_dividend', 'bonus_share', 'transfer_share', "
            "'rights_issue', 'split', 'merge', 'mixed')",
            name="ck_corporate_action_type",
        ),
        sa.CheckConstraint(
            "cash_dividend is null or cash_dividend >= 0",
            name="ck_corporate_action_cash_non_negative",
        ),
        sa.CheckConstraint(
            "bonus_share_ratio is null or bonus_share_ratio >= 0",
            name="ck_corporate_action_bonus_non_negative",
        ),
        sa.CheckConstraint(
            "transfer_share_ratio is null or transfer_share_ratio >= 0",
            name="ck_corporate_action_transfer_non_negative",
        ),
        sa.CheckConstraint(
            "rights_issue_ratio is null or rights_issue_ratio >= 0",
            name="ck_corporate_action_rights_ratio_non_negative",
        ),
        sa.CheckConstraint(
            "rights_issue_price is null or rights_issue_price >= 0",
            name="ck_corporate_action_rights_price_non_negative",
        ),
        sa.CheckConstraint(
            "split_ratio is null or split_ratio > 0",
            name="ck_corporate_action_split_positive",
        ),
        schema="market",
    )
    op.create_index(
        "ix_corporate_action_symbol_ex_date",
        "corporate_action",
        ["symbol", "ex_date"],
        schema="market",
    )
    op.create_index(
        "ix_corporate_action_type_ex_date",
        "corporate_action",
        ["action_type", "ex_date"],
        schema="market",
    )

    op.create_table(
        "adjustment_factor",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("adjust_type", sa.String(length=10), nullable=False),
        sa.Column("factor", sa.Numeric(24, 10), nullable=False),
        sa.Column("source_name", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=50), server_default=sa.text("'v1'"), nullable=False),
        sa.Column(
            "quality_status",
            sa.String(length=20),
            server_default=sa.text("'trusted'"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        sa.UniqueConstraint("symbol", "trade_date", "adjust_type", "source_name", "version"),
        sa.CheckConstraint(
            "adjust_type in ('qfq', 'hfq')",
            name="ck_adjustment_factor_adjust_type",
        ),
        sa.CheckConstraint("factor > 0", name="ck_adjustment_factor_positive"),
        schema="market",
    )
    op.create_index(
        "ix_adjustment_factor_symbol_type_date",
        "adjustment_factor",
        ["symbol", "adjust_type", "trade_date"],
        schema="market",
    )
    op.create_index(
        "ix_adjustment_factor_date_type",
        "adjustment_factor",
        ["trade_date", "adjust_type"],
        schema="market",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_adjustment_factor_date_type", table_name="adjustment_factor", schema="market"
    )
    op.drop_index(
        "ix_adjustment_factor_symbol_type_date",
        table_name="adjustment_factor",
        schema="market",
    )
    op.drop_table("adjustment_factor", schema="market")

    op.drop_index(
        "ix_corporate_action_type_ex_date", table_name="corporate_action", schema="market"
    )
    op.drop_index(
        "ix_corporate_action_symbol_ex_date", table_name="corporate_action", schema="market"
    )
    op.drop_table("corporate_action", schema="market")
