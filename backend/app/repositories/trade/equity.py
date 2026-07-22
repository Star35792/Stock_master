from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.trade import (
    PaperAccountEquityComponent,
    PaperAccountEquitySnapshot,
)


class PaperEquitySnapshotRepository:
    def get_by_idempotency_key(
        self,
        db: Session,
        *,
        idempotency_key: str,
    ) -> PaperAccountEquitySnapshot | None:
        return db.scalar(
            select(PaperAccountEquitySnapshot).where(
                PaperAccountEquitySnapshot.idempotency_key == idempotency_key
            )
        )

    def get_by_account_date(
        self,
        db: Session,
        *,
        account_id: int,
        valuation_date,
    ) -> PaperAccountEquitySnapshot | None:
        return db.scalar(
            select(PaperAccountEquitySnapshot).where(
                PaperAccountEquitySnapshot.account_id == account_id,
                PaperAccountEquitySnapshot.valuation_date == valuation_date,
            )
        )

    def get_latest(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> PaperAccountEquitySnapshot | None:
        return db.scalar(
            select(PaperAccountEquitySnapshot)
            .where(PaperAccountEquitySnapshot.account_id == account_id)
            .order_by(
                PaperAccountEquitySnapshot.valuation_date.desc(),
                PaperAccountEquitySnapshot.id.desc(),
            )
            .limit(1)
        )

    def get_with_components(
        self,
        db: Session,
        *,
        account_id: int,
        snapshot_id: int,
    ) -> tuple[
        PaperAccountEquitySnapshot | None,
        list[PaperAccountEquityComponent],
    ]:
        snapshot = db.scalar(
            select(PaperAccountEquitySnapshot).where(
                PaperAccountEquitySnapshot.id == snapshot_id,
                PaperAccountEquitySnapshot.account_id == account_id,
            )
        )
        if snapshot is None:
            return None, []
        components = list(
            db.scalars(
                select(PaperAccountEquityComponent)
                .where(PaperAccountEquityComponent.snapshot_id == snapshot_id)
                .order_by(PaperAccountEquityComponent.symbol)
            )
        )
        return snapshot, components

    def save(
        self,
        db: Session,
        *,
        snapshot_values: dict,
        component_values: list[dict],
    ) -> PaperAccountEquitySnapshot:
        snapshot = PaperAccountEquitySnapshot(**snapshot_values)
        db.add(snapshot)
        db.flush()
        db.add_all(
            [
                PaperAccountEquityComponent(snapshot_id=snapshot.id, **values)
                for values in component_values
            ]
        )
        return snapshot
