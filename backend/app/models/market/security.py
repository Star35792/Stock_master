from datetime import date

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    Index,
    Sequence,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class Security(TimestampMixin, Base):
    __tablename__ = "security"
    __table_args__ = (
        UniqueConstraint("symbol"),
        CheckConstraint(
            "security_type in ('stock', 'etf', 'index', 'fund', 'bond', 'unknown')",
            name="ck_security_type",
        ),
        CheckConstraint(
            "exchange in ('SH', 'SZ', 'BJ', 'HK', 'UNKNOWN')",
            name="ck_security_exchange",
        ),
        Index("ix_security_type", "security_type"),
        Index("ix_security_exchange", "exchange"),
        Index("ix_security_name", "name"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger,
        Sequence("market_security_id_seq"),
        server_default=text("nextval('market_security_id_seq')"),
        primary_key=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_symbol: Mapped[str | None] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    security_type: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, server_default=text("'CNY'"))
    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    industry: Mapped[str | None] = mapped_column(String(100))
    area: Mapped[str | None] = mapped_column(String(100))
    board: Mapped[str | None] = mapped_column(String(50))
    source_name: Mapped[str | None] = mapped_column(String(50))
