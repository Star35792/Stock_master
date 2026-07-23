from datetime import date
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class FinancialStatement(TimestampMixin, Base):
    __tablename__ = "financial_statement"
    __table_args__ = (
        CheckConstraint(
            "statement_type in ('income', 'balance', 'cash_flow')",
            name="statement_type_allowed",
        ),
        Index(
            "uq_financial_statement_key",
            "symbol",
            "statement_type",
            "period",
            "fiscal_year",
            "fiscal_period",
            unique=True,
        ),
        Index("ix_financial_statement_symbol_period_end", "symbol", "period_end"),
        {"schema": "fundamental"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("fundamental_financial_statement_id_seq"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    statement_type: Mapped[str] = mapped_column(String(20), nullable=False)
    period: Mapped[str] = mapped_column(String(20), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(10), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)


class FinancialIndicator(TimestampMixin, Base):
    __tablename__ = "financial_indicator"
    __table_args__ = (
        Index(
            "uq_financial_indicator_key",
            "symbol",
            "report",
            "ability",
            "indicator_name",
            unique=True,
        ),
        Index("ix_financial_indicator_name_report", "indicator_name", "report"),
        {"schema": "fundamental"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("fundamental_financial_indicator_id_seq"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    report: Mapped[str] = mapped_column(String(10), nullable=False)
    ability: Mapped[str] = mapped_column(String(30), nullable=False)
    indicator_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
