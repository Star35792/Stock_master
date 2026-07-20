from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Date, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class ValuationSnapshot(TimestampMixin, Base):
    __tablename__ = "valuation_snapshot"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source_name"),
        Index("ix_valuation_snapshot_symbol_date", "symbol", "trade_date"),
        Index("ix_valuation_snapshot_date", "trade_date"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    float_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    total_shares: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    float_shares: Mapped[Decimal | None] = mapped_column(Numeric(30, 6))
    pe_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    pb: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    ps_ttm: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    close: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
