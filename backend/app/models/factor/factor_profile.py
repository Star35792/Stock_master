from datetime import date
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class FactorProfile(TimestampMixin, Base):
    __tablename__ = "factor_profile"
    __table_args__ = (
        UniqueConstraint("factor_name", "factor_version", "trade_date"),
        Index("ix_factor_profile_factor_date", "factor_name", "factor_version", "trade_date"),
        {"schema": "factor"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("factor_factor_profile_id_seq"), primary_key=True
    )
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    factor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    universe_size: Mapped[int] = mapped_column(Integer, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer, nullable=False)
    coverage_rate: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    missing_rate: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    mean: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    standard_deviation: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    minimum: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    maximum: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    p01: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    p99: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    extreme_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ic_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    rank_ic_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    ic_5d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    rank_ic_5d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    ic_20d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    rank_ic_20d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    top_quantile_forward_return_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    bottom_quantile_forward_return_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    long_short_forward_return_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    # Multi-horizon endpoint spreads.  The factor pool classifier gates on the
    # endpoint (top-minus-bottom quantile) rather than on the IC alone: a factor
    # can carry a positive IC while its extreme buckets still lose money, and
    # such a factor is not tradable.  ``long_short_forward_return_1d`` predates
    # this gate and is kept for compatibility.
    long_short_forward_return_5d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    long_short_forward_return_20d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    quantile_monotonicity_1d: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    top_quantile_turnover: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    # Eligibility belongs to evidence rows, not the factor definition: the same
    # factor can later be recomputed from an actually observed PIT feed.
    evidence_eligibility: Mapped[str] = mapped_column(
        String(32), nullable=False, default="research_only", server_default=text("'research_only'")
    )
    evidence_eligibility_reason: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        default="unverified_evidence_eligibility",
        server_default=text("'unverified_evidence_eligibility'"),
    )
    code_version: Mapped[str] = mapped_column(String(100), nullable=False)
