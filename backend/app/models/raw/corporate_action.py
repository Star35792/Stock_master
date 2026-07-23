from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Date, Index, Numeric, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RawCorporateAction(Base):
    __tablename__ = "corporate_action"
    __table_args__ = (
        Index("ix_raw_corporate_action_symbol_date", "symbol", "ex_date"),
        Index("ix_raw_corporate_action_batch", "batch_id"),
        Index("ix_raw_corporate_action_hash", "row_hash"),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("raw_corporate_action_id_seq"), primary_key=True
    )
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    dividend_per_share: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    per_share_bonus: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    allotment_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    allotment_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    raw_payload: Mapped[dict | None] = mapped_column(JSON)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
