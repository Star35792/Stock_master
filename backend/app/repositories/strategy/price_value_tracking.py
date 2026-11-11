"""Read-only calendar and curated-price inputs for frozen-list observations."""

import json
from datetime import date
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.market import TradingCalendar
from app.repositories.warm_layer import WarmLayerStore


class PriceValueTrackingRepository:
    def common_open_dates(
        self, db: Session, *, after: date, limit: int,
    ) -> list[date]:
        rows = db.execute(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.exchange.in_(("SH", "SZ", "BJ")),
                TradingCalendar.trade_date > after,
                TradingCalendar.is_open.is_(True),
            )
            .group_by(TradingCalendar.trade_date)
            .having(func.count(func.distinct(TradingCalendar.exchange)) == 3)
            .order_by(TradingCalendar.trade_date)
            .limit(limit)
        )
        return list(rows.scalars())

    def qfq_closes(
        self, *, start_date: date, end_date: date, symbols: list[str],
    ) -> dict:
        if not symbols:
            return {"rows": {}, "dataset": "market.prices_qfq", "input_hash": None}
        store = WarmLayerStore()
        connection = store.connect(attach_control=False)
        try:
            placeholders = ", ".join("?" for _ in symbols)
            records = connection.execute(
                "SELECT symbol, trade_date, close FROM market.prices_qfq "
                "WHERE trade_date BETWEEN ? AND ? "
                f"AND symbol IN ({placeholders}) ORDER BY symbol, trade_date",
                [start_date, end_date, *symbols],
            ).fetchall()
        finally:
            connection.close()
        rows = {
            (str(symbol).upper(), trade_date): Decimal(str(close))
            for symbol, trade_date, close in records
            if close is not None
        }
        stable = [
            (symbol, trade_date.isoformat(), str(close))
            for (symbol, trade_date), close in sorted(rows.items())
        ]
        return {
            "rows": rows,
            "dataset": "curated/market.prices_qfq",
            "input_hash": sha256(
                json.dumps(stable, separators=(",", ":")).encode()
            ).hexdigest(),
        }
