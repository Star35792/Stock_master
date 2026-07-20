"""add factor security type applicability

Revision ID: 20260717_0006
Revises: 20260717_0005
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260717_0006"
down_revision: str | None = "20260717_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "factor_definition",
        sa.Column(
            "applicable_security_types",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[\"stock\", \"etf\", \"index\"]'::jsonb"),
            nullable=False,
        ),
        schema="factor",
    )


def downgrade() -> None:
    op.drop_column(
        "factor_definition",
        "applicable_security_types",
        schema="factor",
    )
