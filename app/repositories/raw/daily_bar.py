from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.raw import RawDailyBar


class RawDailyBarRepository:
    def latest_candidates_by_source(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str = "none",
        source_name: str | None = None,
    ) -> dict[tuple[str, date, str], list[RawDailyBar]]:
        rows = self._list_candidates(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
        )

        latest_by_source: dict[tuple[str, date, str, str], RawDailyBar] = {}
        for row in rows:
            key = (row.symbol, row.trade_date, row.adjust_type, row.source_name)
            latest_by_source.setdefault(key, row)

        grouped: dict[tuple[str, date, str], list[RawDailyBar]] = {}
        for row in latest_by_source.values():
            grouped.setdefault((row.symbol, row.trade_date, row.adjust_type), []).append(row)
        return grouped

    def latest_candidates(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        adjust_type: str = "none",
        source_name: str | None = None,
    ) -> list[RawDailyBar]:
        rows = self._list_candidates(
            db,
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            source_name=source_name,
        )

        latest: dict[tuple[str, date, str], RawDailyBar] = {}
        for row in rows:
            key = (row.symbol, row.trade_date, row.adjust_type)
            latest.setdefault(key, row)
        return list(latest.values())

    def _list_candidates(
        self,
        db: Session,
        *,
        symbols: list[str] | None,
        start_date: date | None,
        end_date: date | None,
        adjust_type: str,
        source_name: str | None,
    ) -> list[RawDailyBar]:
        filters = [RawDailyBar.adjust_type == adjust_type]
        if symbols:
            filters.append(RawDailyBar.symbol.in_([symbol.upper() for symbol in symbols]))
        if start_date:
            filters.append(RawDailyBar.trade_date >= start_date)
        if end_date:
            filters.append(RawDailyBar.trade_date <= end_date)
        if source_name:
            filters.append(RawDailyBar.source_name == source_name)

        rows = db.scalars(
            select(RawDailyBar)
            .where(*filters)
            .order_by(
                RawDailyBar.symbol,
                RawDailyBar.trade_date,
                RawDailyBar.adjust_type,
                RawDailyBar.source_priority,
                RawDailyBar.imported_at.desc(),
                RawDailyBar.id.desc(),
            )
        ).all()
        return list(rows)
