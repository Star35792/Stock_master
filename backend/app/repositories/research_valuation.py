"""ORM-side reads for the valuation history (the control-plane twin).

Why this exists
---------------

``app/repositories/research_read.py`` reads the *research* valuation history
from the Parquet warm layer.  The backend-layer rules require the ORM
repository and the warm repository to be **twins**: the same query semantics
must exist on both paths, with identical column names, ordering and filters.

The warm path reads ``research.valuation_history``, the Parquet table the
Eastmoney sweep publishes.  The control plane carries the same facts in
``market.valuation_snapshot``, but only for the days that were captured live.
Both paths therefore answer the same question — "what did the valuation
snapshot look like for this symbol on this date" — from the store each path
owns.

The two paths are **not** interchangeable in coverage: the control plane only
holds the trading days that were captured while the platform was running,
while the warm layer holds the full backfill.  A caller that needs history
beyond the control-plane window must use the warm path.

Scope
-----

Only the columns that both stores share are exposed.  ``PB_MRQ`` has no
counterpart in the control plane (it is called ``pb`` there), so the ORM
twin reports it as ``None`` rather than pretending the two are the same
field.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market.valuation_snapshot import ValuationSnapshot


class ResearchValuationReadRepository:
    """Read the valuation history from the control plane."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_valuation_history(
        self,
        *,
        start_date: date,
        end_date: date,
        symbols: Sequence[str] | None = None,
    ) -> list[SimpleNamespace]:
        """Return valuation rows for the requested window.

        The ordering matches the warm repository (``trade_date, symbol``)
        because the research contract hashes a normalized slice in which row
        order is part of reproducibility.
        """
        statement = select(ValuationSnapshot).where(
            ValuationSnapshot.trade_date >= start_date,
            ValuationSnapshot.trade_date <= end_date,
        )
        if symbols:
            statement = statement.where(
                ValuationSnapshot.symbol.in_(
                    [symbol.upper() for symbol in symbols]
                )
            )
        statement = statement.order_by(
            ValuationSnapshot.trade_date, ValuationSnapshot.symbol
        )
        return [
            SimpleNamespace(
                symbol=row.symbol.upper(),
                trade_date=row.trade_date,
                close=row.close,
                pe_ttm=row.pe_ttm,
                # The control plane stores the price-to-book ratio under
                # ``pb``.  It is the same supplier field the warm layer calls
                # ``PB_MRQ``, but the control plane never recorded whether the
                # ratio switched on the reporting period end, so the twin
                # reports it as unavailable instead of implying it is
                # point-in-time safe.
                pb_mrq=None,
                ps_ttm=row.ps_ttm,
                total_market_cap=row.total_market_cap,
                total_shares=row.total_shares,
                free_shares_a=row.float_shares,
                float_market_cap=row.float_market_cap,
                change_rate=None,
                # The control plane rows were captured live, so they are
                # point-in-time observations of that evening's snapshot.  The
                # warm layer's backfilled rows are not, and the flag makes the
                # difference visible to a consumer.
                pit_verified=True,
            )
            for row in self._db.scalars(statement)
        ]

    def valuation_history_coverage(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Return the coverage facts of the control-plane valuation history."""
        rows = self._db.execute(
            select(
                ValuationSnapshot.trade_date,
                ValuationSnapshot.symbol,
            ).where(
                ValuationSnapshot.trade_date >= start_date,
                ValuationSnapshot.trade_date <= end_date,
            )
        ).fetchall()
        if not rows:
            return {
                "row_count": 0,
                "trading_days": 0,
                "symbol_count": 0,
                "min_date": None,
                "max_date": None,
            }
        trade_dates = [row[0] for row in rows]
        symbols = {row[1] for row in rows}
        return {
            "row_count": len(rows),
            "trading_days": len(set(trade_dates)),
            "symbol_count": len(symbols),
            "min_date": min(trade_dates),
            "max_date": max(trade_dates),
        }


__all__ = ["ResearchValuationReadRepository"]
