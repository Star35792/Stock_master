from datetime import date
from decimal import Decimal

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.market import DailyBar, Security, TradingCalendar
from app.models.raw import RawDailyBar


class DailyBarRepository:
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
            select(DailyBar.trade_date, func.count())
            .where(*filters)
            .group_by(DailyBar.trade_date)
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
                select(DailyBar)
                .where(*filters)
                .order_by(DailyBar.symbol, DailyBar.trade_date)
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
            index_elements=[DailyBar.symbol, DailyBar.trade_date, DailyBar.adjust_type],
            set_=update_columns,
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
        if quality_status:
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
