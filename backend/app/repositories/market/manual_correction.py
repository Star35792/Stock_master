from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import ManualCorrection


class ManualCorrectionRepository:
    def list(
        self,
        db: Session,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str | None = None,
        field_name: str | None = None,
        corrected_by: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ManualCorrection], int]:
        filters = []
        if symbol:
            filters.append(ManualCorrection.symbol == symbol)
        if start_date:
            filters.append(ManualCorrection.trade_date >= start_date)
        if end_date:
            filters.append(ManualCorrection.trade_date <= end_date)
        if adjust_type:
            filters.append(ManualCorrection.adjust_type == adjust_type)
        if field_name:
            filters.append(ManualCorrection.field_name == field_name)
        if corrected_by:
            filters.append(ManualCorrection.corrected_by == corrected_by)

        total = db.scalar(select(func.count()).select_from(ManualCorrection).where(*filters)) or 0
        items = list(
            db.scalars(
                select(ManualCorrection)
                .where(*filters)
                .order_by(
                    ManualCorrection.trade_date.desc(),
                    ManualCorrection.symbol,
                    ManualCorrection.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def create(
        self,
        db: Session,
        *,
        dataset: str,
        symbol: str,
        trade_date: date,
        adjust_type: str,
        field_name: str,
        old_value: str | None,
        new_value: str,
        reason: str,
        corrected_by: str,
    ) -> ManualCorrection:
        correction = ManualCorrection(
            dataset=dataset,
            symbol=symbol,
            trade_date=trade_date,
            adjust_type=adjust_type,
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            corrected_by=corrected_by,
            applied_at=db.scalar(select(func.clock_timestamp())),
        )
        db.add(correction)
        return correction
