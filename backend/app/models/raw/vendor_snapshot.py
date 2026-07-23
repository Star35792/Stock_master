from datetime import date

from sqlalchemy import JSON, BigInteger, Date, Index, Sequence, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RawVendorSnapshot(Base):
    __tablename__ = "vendor_snapshot"
    __table_args__ = (
        Index("ix_raw_vendor_snapshot_dataset_date", "dataset", "snapshot_date"),
        Index("ix_raw_vendor_snapshot_batch", "batch_id"),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(
        BigInteger, Sequence("raw_vendor_snapshot_id_seq"), primary_key=True
    )
    batch_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
