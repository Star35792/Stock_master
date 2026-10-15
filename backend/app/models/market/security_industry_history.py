from datetime import date

from sqlalchemy import (
    BigInteger,
    Date,
    Index,
    Sequence,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class SecurityIndustryHistory(TimestampMixin, Base):
    """Point-in-time industry classification for ``market.security``.

    ``market.security.industry`` only carries the *current* label. Re-running a
    historical factor or backtest against it backfills today's classification
    into the past. This table records each industry label with the window it was
    effective for, so consumers can resolve the classification as of any trade
    date. ``effective_to`` is NULL for the open (still effective) interval.
    """

    __tablename__ = "security_industry_history"
    __table_args__ = (
        Index(
            "uq_security_industry_history_from",
            "symbol",
            "effective_from",
            unique=True,
        ),
        Index(
            "ix_security_industry_history_window",
            "symbol",
            "effective_from",
            "effective_to",
        ),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("market_security_industry_history_id_seq"),
        server_default=text("nextval('market_security_industry_history_id_seq')"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
