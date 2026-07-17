from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class FactorValue(TimestampMixin, Base):
    __tablename__ = "factor_value"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "factor_name", "factor_version"),
        ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        Index("ix_factor_value_factor_date", "factor_name", "factor_version", "trade_date"),
        Index("ix_factor_value_symbol_factor_date", "symbol", "factor_name", "trade_date"),
        {"schema": "factor"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(50), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    run_id: Mapped[int] = mapped_column(ForeignKey("factor.factor_run.id"), nullable=False)
