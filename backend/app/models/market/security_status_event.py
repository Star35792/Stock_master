from datetime import date
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class SecurityStatusEvent(TimestampMixin, Base):
    """Point-in-time trading status for individual securities.

    ``market.daily_bar`` only carries tradable-day price records. This table
    answers why a bar may be missing on a given exchange open date: the
    security could be suspended, ST, *ST, in delisting proceedings, not yet
    listed, or already delisted. It is the single source of truth for
    distinguishing "real suspension" from "data missing".
    """

    __tablename__ = "security_status_event"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "status", "source_name"),
        CheckConstraint(
            "status in ('normal', 'suspended', 'st', 'star_st', 'delisting', "
            "'unlisted', 'delisted')",
            name="ck_security_status_event_status",
        ),
        CheckConstraint(
            "effective_from is null or effective_to is null or effective_from <= effective_to",
            name="ck_security_status_event_effective_window",
        ),
        Index("ix_security_status_event_symbol_date", "symbol", "trade_date"),
        Index("ix_security_status_event_status_date", "status", "trade_date"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("market_security_status_event_id_seq"),
        server_default=text("nextval('market_security_status_event_id_seq')"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(20))
    batch_id: Mapped[int | None] = mapped_column(BigInteger)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
