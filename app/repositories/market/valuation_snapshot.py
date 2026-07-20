from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.market import ValuationSnapshot


class ValuationSnapshotRepository:
    def upsert_many(
        self,
        db: Session,
        *,
        rows: list[dict[str, Any]],
    ) -> int:
        if not rows:
            return 0
        statement = insert(ValuationSnapshot).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=[
                ValuationSnapshot.symbol,
                ValuationSnapshot.trade_date,
                ValuationSnapshot.source_name,
            ],
            set_={
                "total_market_cap": statement.excluded.total_market_cap,
                "float_market_cap": statement.excluded.float_market_cap,
                "total_shares": statement.excluded.total_shares,
                "float_shares": statement.excluded.float_shares,
                "pe_ttm": statement.excluded.pe_ttm,
                "pb": statement.excluded.pb,
                "ps_ttm": statement.excluded.ps_ttm,
                "close": statement.excluded.close,
                "currency": statement.excluded.currency,
                "raw_payload": statement.excluded.raw_payload,
            },
        )
        db.execute(statement)
        return len(rows)

    def list_available(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
    ) -> list[ValuationSnapshot]:
        filters = [
            ValuationSnapshot.trade_date >= start_date,
            ValuationSnapshot.trade_date <= end_date,
        ]
        if symbols:
            filters.append(ValuationSnapshot.symbol.in_(symbols))

        return list(
            db.scalars(
                select(ValuationSnapshot)
                .where(*filters)
                .order_by(ValuationSnapshot.symbol, ValuationSnapshot.trade_date)
            )
        )

    def latest_market_cap_by_symbol(
        self,
        db: Session,
        *,
        trade_date: date,
        symbols: list[str] | None = None,
    ) -> dict[str, Decimal]:
        rows = self.list_available(
            db,
            start_date=trade_date,
            end_date=trade_date,
            symbols=symbols,
        )
        return {
            row.symbol: row.total_market_cap
            for row in rows
            if row.total_market_cap is not None
        }
