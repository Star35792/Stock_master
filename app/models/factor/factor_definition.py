from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class FactorDefinition(TimestampMixin, Base):
    __tablename__ = "factor_definition"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            "frequency in ('daily', 'weekly', 'monthly')",
            name="ck_factor_definition_frequency",
        ),
        CheckConstraint(
            "window_days is null or window_days > 0",
            name="ck_factor_definition_window_positive",
        ),
        Index("ix_factor_definition_name_version", "name", "version"),
        Index("ix_factor_definition_active_frequency", "is_active", "frequency"),
        {"schema": "factor"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(String(20), nullable=False)
    inputs: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    window_days: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    dependencies: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    parameters_schema: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
