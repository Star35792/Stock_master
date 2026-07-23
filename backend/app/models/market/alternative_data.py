from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, BigInteger, Date, Index, Integer, Numeric, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.models.common import TimestampMixin


class AlternativeDataSnapshot(TimestampMixin, Base):
    __tablename__ = "alternative_data_snapshot"
    __table_args__ = (
        Index(
            "uq_alternative_snapshot_key",
            "dataset",
            "snapshot_date",
            "item_key",
            unique=True,
        ),
        Index("ix_alternative_snapshot_symbol_date", "symbol", "snapshot_date"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("market_alternative_data_snapshot_id_seq"), primary_key=True
    )
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_key: Mapped[str] = mapped_column(String(150), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(20))
    rank: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[Decimal | None] = mapped_column(Numeric(30, 10))
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)


class IndexConstituentSnapshot(TimestampMixin, Base):
    __tablename__ = "index_constituent_snapshot"
    __table_args__ = (
        Index(
            "uq_index_constituent_snapshot_key",
            "snapshot_date",
            "index_symbol",
            "member_symbol",
            unique=True,
        ),
        Index("ix_index_constituent_member_date", "member_symbol", "snapshot_date"),
        {"schema": "market"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("market_index_constituent_snapshot_id_seq"), primary_key=True
    )
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    index_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    member_symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    member_name: Mapped[str] = mapped_column(String(100), nullable=False)
    weight: Mapped[Decimal | None] = mapped_column(Numeric(20, 10))
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
