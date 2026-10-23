from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class AdjustmentFactor(TimestampMixin, Base):
    __tablename__ = "adjustment_factor"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "adjust_type", "source_name", "version"),
        CheckConstraint(
            "adjust_type in ('qfq', 'hfq')",
            name="ck_adjustment_factor_adjust_type",
        ),
        CheckConstraint("factor > 0", name="ck_adjustment_factor_positive"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("market_adjustment_factor_id_seq"),
        server_default=text("nextval('market_adjustment_factor_id_seq')"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    adjust_type: Mapped[str] = mapped_column(String(10), nullable=False)
    factor: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False, server_default=text("'v1'"))
    release_id: Mapped[str | None] = mapped_column(String(64))
    quality_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'trusted'")
    )
