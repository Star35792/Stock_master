"""allow reopened data quality issues

Revision ID: 20260717_0004
Revises: 20260717_0003
Create Date: 2026-07-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260717_0004"
down_revision: str | None = "20260717_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_quality_issue_status_allowed",
        "data_quality_issue",
        schema="system",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quality_issue_status_allowed",
        "data_quality_issue",
        "status in ('open', 'ignored', 'fixed', 'reopened', 'false_positive')",
        schema="system",
    )


def downgrade() -> None:
    op.execute(
        """
        update system.data_quality_issue
        set status = 'open'
        where status = 'reopened'
        """
    )
    op.drop_constraint(
        "ck_quality_issue_status_allowed",
        "data_quality_issue",
        schema="system",
        type_="check",
    )
    op.create_check_constraint(
        "ck_quality_issue_status_allowed",
        "data_quality_issue",
        "status in ('open', 'ignored', 'fixed', 'false_positive')",
        schema="system",
    )
