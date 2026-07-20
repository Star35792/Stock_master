from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class CorporateAction(TimestampMixin, Base):
    __tablename__ = "corporate_action"
    __table_args__ = (
        UniqueConstraint("symbol", "ex_date", "action_type", "source_name"),
        ForeignKeyConstraint(["symbol"], ["market.security.symbol"]),
        CheckConstraint(
            "action_type in ('cash_dividend', 'bonus_share', 'transfer_share', "
            "'rights_issue', 'split', 'merge', 'mixed')",
            name="ck_corporate_action_type",
        ),
        CheckConstraint(
            "cash_dividend is null or cash_dividend >= 0",
            name="ck_corporate_action_cash_non_negative",
        ),
        CheckConstraint(
            "bonus_share_ratio is null or bonus_share_ratio >= 0",
            name="ck_corporate_action_bonus_non_negative",
        ),
        CheckConstraint(
            "transfer_share_ratio is null or transfer_share_ratio >= 0",
            name="ck_corporate_action_transfer_non_negative",
        ),
        CheckConstraint(
            "rights_issue_ratio is null or rights_issue_ratio >= 0",
            name="ck_corporate_action_rights_ratio_non_negative",
        ),
        CheckConstraint(
            "rights_issue_price is null or rights_issue_price >= 0",
            name="ck_corporate_action_rights_price_non_negative",
        ),
        CheckConstraint(
            "split_ratio is null or split_ratio > 0",
            name="ck_corporate_action_split_positive",
        ),
        Index("ix_corporate_action_symbol_ex_date", "symbol", "ex_date"),
        Index("ix_corporate_action_type_ex_date", "action_type", "ex_date"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)
    cash_dividend: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    bonus_share_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    transfer_share_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    rights_issue_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    rights_issue_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    split_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    record_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_event_id: Mapped[str | None] = mapped_column(String(100))
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
