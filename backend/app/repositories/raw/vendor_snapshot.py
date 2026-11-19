from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.raw import RawVendorSnapshot


@dataclass(frozen=True)
class CapturedVendorSnapshot:
    snapshot_id: int
    batch_id: int
    inserted: bool


class RawVendorSnapshotRepository:
    def add_if_changed(
        self, db: Session, snapshot: RawVendorSnapshot,
    ) -> CapturedVendorSnapshot:
        """Retain one immutable version per source/business day/content hash."""
        existing = db.scalar(
            select(RawVendorSnapshot)
            .where(
                RawVendorSnapshot.source_name == snapshot.source_name,
                RawVendorSnapshot.dataset == snapshot.dataset,
                RawVendorSnapshot.snapshot_date == snapshot.snapshot_date,
                RawVendorSnapshot.row_hash == snapshot.row_hash,
            )
            .order_by(RawVendorSnapshot.id)
            .limit(1)
        )
        if existing is not None:
            return CapturedVendorSnapshot(existing.id, existing.batch_id, False)
        db.add(snapshot)
        db.flush()
        return CapturedVendorSnapshot(snapshot.id, snapshot.batch_id, True)
