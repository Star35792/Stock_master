from datetime import date

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RawFinancialPayload(Base):
    __tablename__ = "financial_payload"
    __table_args__ = (
        CheckConstraint(
            "dataset in ('income', 'balance', 'cash_flow', 'indicators')",
            name="dataset_allowed",
        ),
        Index("ix_raw_financial_symbol_dataset", "symbol", "dataset"),
        Index("ix_raw_financial_batch", "batch_id"),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("raw.import_batch.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    dataset: Mapped[str] = mapped_column(String(20), nullable=False)
    report_key: Mapped[str] = mapped_column(String(20), nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
