from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import AdjustmentFactor


class AdjustmentFactorRepository:
    def list(
        self,
        db: Session,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str | None = None,
        source_name: str | None = None,
        version: str | None = None,
        quality_status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[AdjustmentFactor], int]:
        filters = []
        if symbol:
            filters.append(AdjustmentFactor.symbol == symbol)
        if start_date:
            filters.append(AdjustmentFactor.trade_date >= start_date)
        if end_date:
            filters.append(AdjustmentFactor.trade_date <= end_date)
        if adjust_type:
            filters.append(AdjustmentFactor.adjust_type == adjust_type)
        if source_name:
            filters.append(AdjustmentFactor.source_name == source_name)
        if version:
            filters.append(AdjustmentFactor.version == version)
        if quality_status:
            filters.append(AdjustmentFactor.quality_status == quality_status)

        total = db.scalar(select(func.count()).select_from(AdjustmentFactor).where(*filters)) or 0
        items = list(
            db.scalars(
                select(AdjustmentFactor)
                .where(*filters)
                .order_by(
                    AdjustmentFactor.trade_date.desc(),
                    AdjustmentFactor.symbol,
                    AdjustmentFactor.adjust_type,
                    AdjustmentFactor.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
