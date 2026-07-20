from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class RawVendorSnapshot(Base):
    __tablename__ = "vendor_snapshot"
    __table_args__ = (
        Index("ix_raw_vendor_snapshot_dataset_date", "dataset", "snapshot_date"),
        Index("ix_raw_vendor_snapshot_batch", "batch_id"),
        {"schema": "raw"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("raw.import_batch.id"), nullable=False)
    source_name: Mapped[str] = mapped_column(String(50), nullable=False)
    dataset: Mapped[str] = mapped_column(String(50), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
