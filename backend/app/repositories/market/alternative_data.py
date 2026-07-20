from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import AlternativeDataSnapshot


class AlternativeDataSnapshotRepository:
    def list_available(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        datasets: list[str],
    ) -> list[AlternativeDataSnapshot]:
        if not datasets:
            return []
        return list(
            db.scalars(
                select(AlternativeDataSnapshot)
                .where(
                    AlternativeDataSnapshot.snapshot_date >= start_date,
                    AlternativeDataSnapshot.snapshot_date <= end_date,
                    AlternativeDataSnapshot.dataset.in_(datasets),
                )
                .order_by(
                    AlternativeDataSnapshot.dataset,
                    AlternativeDataSnapshot.snapshot_date,
                    AlternativeDataSnapshot.symbol,
                    AlternativeDataSnapshot.item_key,
                )
            )
        )
