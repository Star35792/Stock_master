from datetime import date
from decimal import Decimal

from sqlalchemy import and_, desc, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.market import DailyBar, Security, TradingCalendar
from app.models.raw import RawDailyBar


class DailyBarRepository:
    def get_latest_consumable(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
    ) -> DailyBar | None:
        return db.scalar(
            select(DailyBar)
            .where(
                DailyBar.symbol == symbol,
                DailyBar.adjust_type == adjust_type,
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.is_suspect.is_(False),
            )
            .order_by(desc(DailyBar.trade_date))
            .limit(1)
        )

    def list_latest_consumable_by_symbols(
        self,
        db: Session,
        *,
        symbols: set[str],
        adjust_type: str,
    ) -> list[DailyBar]:
        """Batch version of ``get_latest_consumable`` for read-only analysis.

        One trusted/manual-fixed, non-suspect bar per symbol via a window
        function, so a holdings view does not issue one query per symbol.
        (The single-symbol paged variant is ``list_latest_consumable`` below.)
        """
        if not symbols:
            return []
        ranked = (
            select(
                DailyBar.id.label("bar_id"),
                func.row_number()
                .over(
                    partition_by=DailyBar.symbol,
                    order_by=(desc(DailyBar.trade_date), desc(DailyBar.id)),
                )
                .label("rn"),
            )
            .where(
                DailyBar.symbol.in_(symbols),
                DailyBar.adjust_type == adjust_type,
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.is_suspect.is_(False),
            )
            .subquery()
        )
        return list(
            db.scalars(
                select(DailyBar)
                .join(ranked, DailyBar.id == ranked.c.bar_id)
                .where(ranked.c.rn == 1)
            )
        )

    def count_trusted_by_date(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None = None,
    ) -> dict[date, int]:
        filters = [
            DailyBar.trade_date >= start_date,
            DailyBar.trade_date <= end_date,
            DailyBar.adjust_type == adjust_type,
            DailyBar.quality_status == "trusted",
            DailyBar.is_suspect.is_(False),
        ]
        if symbols:
            filters.append(DailyBar.symbol.in_(symbols))
        rows = db.execute(
            select(DailyBar.trade_date, func.count()).where(*filters).group_by(DailyBar.trade_date)
        ).all()
        return dict(rows)

    def list_for_factor_calculation(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None = None,
        quality_status: str = "trusted",
    ) -> list[DailyBar]:
        filters = [
            DailyBar.trade_date >= start_date,
            DailyBar.trade_date <= end_date,
            DailyBar.adjust_type == adjust_type,
            DailyBar.quality_status == quality_status,
            DailyBar.is_suspect.is_(False),
        ]
        if symbols:
            filters.append(DailyBar.symbol.in_(symbols))

        return list(
            db.scalars(
                select(DailyBar).where(*filters).order_by(DailyBar.symbol, DailyBar.trade_date)
            )
        )

    def get_by_key(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        adjust_type: str,
    ) -> DailyBar | None:
        return db.scalar(
            select(DailyBar).where(
                DailyBar.symbol == symbol,
                DailyBar.trade_date == trade_date,
                DailyBar.adjust_type == adjust_type,
            )
        )

    def list_by_symbols_on_date(
        self,
        db: Session,
        *,
        symbols: set[str],
        trade_date: date,
        adjust_type: str,
    ) -> list[DailyBar]:
        if not symbols:
            return []
        return list(
            db.scalars(
                select(DailyBar)
                .where(
                    DailyBar.symbol.in_(symbols),
                    DailyBar.trade_date == trade_date,
                    DailyBar.adjust_type == adjust_type,
                )
                .order_by(DailyBar.symbol)
            )
        )

    def list_by_symbols_on_date_for_update(
        self,
        db: Session,
        *,
        symbols: set[str],
        trade_date: date,
        adjust_type: str,
    ) -> list[DailyBar]:
        if not symbols:
            return []
        return list(
            db.scalars(
                select(DailyBar)
                .where(
                    DailyBar.symbol.in_(symbols),
                    DailyBar.trade_date == trade_date,
                    DailyBar.adjust_type == adjust_type,
                )
                .order_by(DailyBar.symbol)
            )
        )

    def get_latest_trusted_none_dates(
        self,
        db: Session,
        *,
        symbols: set[str],
    ) -> dict[str, date]:
        if not symbols:
            return {}
        rows = db.execute(
            select(DailyBar.symbol, func.max(DailyBar.trade_date))
            .where(
                DailyBar.symbol.in_(symbols),
                DailyBar.adjust_type == "none",
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.is_suspect.is_(False),
            )
            .group_by(DailyBar.symbol)
        ).all()
        return dict(rows)

    def list_recent_trusted_none_amounts(
        self,
        db: Session,
        *,
        symbols: set[str],
        end_date: date,
        window: int,
    ) -> list[object]:
        if not symbols:
            return []
        row_number = func.row_number().over(
            partition_by=DailyBar.symbol,
            order_by=DailyBar.trade_date.desc(),
        )
        ranked = (
            select(
                DailyBar.id.label("daily_bar_id"),
                DailyBar.symbol,
                DailyBar.trade_date,
                DailyBar.amount,
                DailyBar.source_name,
                DailyBar.quality_status,
                DailyBar.updated_at,
                row_number.label("row_number"),
            )
            .where(
                DailyBar.symbol.in_(symbols),
                DailyBar.trade_date <= end_date,
                DailyBar.adjust_type == "none",
                DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                DailyBar.is_suspect.is_(False),
            )
            .subquery()
        )
        return list(
            db.execute(
                select(ranked)
                .where(ranked.c.row_number <= window)
                .order_by(ranked.c.symbol, ranked.c.trade_date)
            ).all()
        )

    def apply_manual_correction(
        self,
        db: Session,
        *,
        daily_bar: DailyBar,
        field_name: str,
        value: object,
    ) -> None:
        setattr(daily_bar, field_name, value)
        daily_bar.quality_status = "manual_fixed"
        daily_bar.is_suspect = False
        db.add(daily_bar)

    def upsert_from_raw(
        self,
        db: Session,
        *,
        raw_bar: RawDailyBar,
        quality_score: Decimal,
        quality_status: str,
        is_suspect: bool,
        source_count: int = 1,
    ) -> None:
        row = {
            "symbol": raw_bar.symbol,
            "security_type": raw_bar.security_type,
            "trade_date": raw_bar.trade_date,
            "adjust_type": raw_bar.adjust_type,
            "open": raw_bar.open,
            "high": raw_bar.high,
            "low": raw_bar.low,
            "close": raw_bar.close,
            "pre_close": raw_bar.pre_close,
            "change_amount": raw_bar.change_amount,
            "pct_change": raw_bar.pct_change,
            "volume": raw_bar.volume,
            "amount": raw_bar.amount,
            "turnover_rate": raw_bar.turnover_rate,
            "amplitude": raw_bar.amplitude,
            "limit_up": getattr(raw_bar, "limit_up", None),
            "limit_down": getattr(raw_bar, "limit_down", None),
            "is_suspended": getattr(raw_bar, "is_suspended", False),
            "special_treatment_status": getattr(raw_bar, "special_treatment_status", None),
            "source_name": raw_bar.source_name,
            "source_count": source_count,
            "quality_score": quality_score,
            "quality_status": quality_status,
            "is_suspect": is_suspect,
            "selected_raw_id": raw_bar.id,
        }
        statement = insert(DailyBar).values(row)
        update_columns = {
            key: getattr(statement.excluded, key)
            for key in row
            if key not in {"symbol", "trade_date", "adjust_type"}
        }
        statement = statement.on_conflict_do_update(
            index_elements=[
                DailyBar.symbol,
                DailyBar.trade_date,
                DailyBar.adjust_type,
            ],
            set_=update_columns,
            where=DailyBar.quality_status != "manual_fixed",
        )
        db.execute(statement)

    def list_by_symbol(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        quality_status: str | None = "trusted",
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[DailyBar], int]:
        filters = [
            DailyBar.symbol == symbol,
            DailyBar.adjust_type == adjust_type,
        ]
        if start_date:
            filters.append(DailyBar.trade_date >= start_date)
        if end_date:
            filters.append(DailyBar.trade_date <= end_date)
        if quality_status == "consumable":
            filters.extend(
                [
                    DailyBar.quality_status.in_(("trusted", "manual_fixed")),
                    DailyBar.is_suspect.is_(False),
                ]
            )
        elif quality_status:
            filters.append(DailyBar.quality_status == quality_status)

        total = db.scalar(select(func.count()).select_from(DailyBar).where(*filters)) or 0
        items = list(
            db.scalars(
                select(DailyBar)
                .where(*filters)
                .order_by(DailyBar.trade_date)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_latest_consumable(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 320,
    ) -> tuple[list[DailyBar], int]:
        filters = [
            DailyBar.symbol == symbol,
            DailyBar.adjust_type == adjust_type,
            DailyBar.quality_status.in_(("trusted", "manual_fixed")),
            DailyBar.is_suspect.is_(False),
        ]
        if start_date:
            filters.append(DailyBar.trade_date >= start_date)
        if end_date:
            filters.append(DailyBar.trade_date <= end_date)

        total = db.scalar(select(func.count()).select_from(DailyBar).where(*filters)) or 0
        latest = list(
            db.scalars(
                select(DailyBar)
                .where(*filters)
                .order_by(DailyBar.trade_date.desc())
                .limit(limit)
            )
        )
        latest.reverse()
        return latest, total

    def list_missing_by_calendar(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbol: str | None = None,
        security_type: str | None = None,
        exchange: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[object], int]:
        filters = [
            TradingCalendar.is_open.is_(True),
            TradingCalendar.trade_date >= start_date,
            TradingCalendar.trade_date <= end_date,
            or_(Security.list_date.is_(None), Security.list_date <= TradingCalendar.trade_date),
            or_(
                Security.delist_date.is_(None),
                Security.delist_date >= TradingCalendar.trade_date,
            ),
            DailyBar.id.is_(None),
        ]
        if symbol:
            filters.append(Security.symbol == symbol)
        if security_type:
            filters.append(Security.security_type == security_type)
        if exchange:
            filters.append(Security.exchange == exchange)

        base = (
            select(
                Security.symbol,
                Security.name,
                Security.security_type,
                Security.exchange,
                TradingCalendar.trade_date,
                literal(adjust_type).label("adjust_type"),
            )
            .select_from(Security)
            .join(TradingCalendar, TradingCalendar.exchange == Security.exchange)
            .outerjoin(
                DailyBar,
                and_(
                    DailyBar.symbol == Security.symbol,
                    DailyBar.trade_date == TradingCalendar.trade_date,
                    DailyBar.adjust_type == adjust_type,
                ),
            )
            .where(*filters)
        )

        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
        items = list(
            db.execute(
                base.order_by(TradingCalendar.trade_date, Security.symbol)
                .limit(limit)
                .offset(offset)
            ).all()
        )
        return items, total
