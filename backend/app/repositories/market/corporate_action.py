from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import CorporateAction


class CorporateActionRepository:
    def list_for_symbols_date_range(
        self,
        db: Session,
        *,
        symbols: set[str],
        start_date: date,
        end_date: date,
    ) -> list[CorporateAction]:
        if not symbols:
            return []
        return list(
            db.scalars(
                select(CorporateAction)
                .where(
                    CorporateAction.symbol.in_(symbols),
                    CorporateAction.ex_date >= start_date,
                    CorporateAction.ex_date <= end_date,
                )
                .order_by(CorporateAction.ex_date, CorporateAction.symbol)
            )
        )

    def list(
        self,
        db: Session,
        *,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        action_type: str | None = None,
        source_name: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CorporateAction], int]:
        filters = []
        if symbol:
            filters.append(CorporateAction.symbol == symbol)
        if start_date:
            filters.append(CorporateAction.ex_date >= start_date)
        if end_date:
            filters.append(CorporateAction.ex_date <= end_date)
        if action_type:
            filters.append(CorporateAction.action_type == action_type)
        if source_name:
            filters.append(CorporateAction.source_name == source_name)

        total = db.scalar(select(func.count()).select_from(CorporateAction).where(*filters)) or 0
        items = list(
            db.scalars(
                select(CorporateAction)
                .where(*filters)
                .order_by(
                    CorporateAction.ex_date.desc(),
                    CorporateAction.symbol,
                    CorporateAction.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total
