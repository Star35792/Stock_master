from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import SecurityStatusEvent


class SecurityStatusEventRepository:
    """Point-in-time trading status lookups.

    ``market.daily_bar`` only contains tradable-day price records. This
    repository answers whether a missing bar is explained by a real corporate
    status event (suspension, ST, *ST, delisting, not yet listed, already
    delisted) or by a true data gap.
    """

    ALLOWED_STATUSES = frozenset(
        {"normal", "suspended", "st", "star_st", "delisting", "unlisted", "delisted"}
    )

    def statuses_on_date(
        self,
        db: Session,
        *,
        symbols: set[str] | list[str],
        trade_date: date,
    ) -> dict[str, str]:
        """Return the most recent status per symbol as of ``trade_date``.

        Status rows are daily observations; the latest row on or before the
        requested date wins. Symbols without any history are absent from the
        result so callers can choose their own fallback policy.
        """
        symbol_set = {symbol for symbol in symbols if symbol}
        if not symbol_set:
            return {}

        rows = db.execute(
            select(
                SecurityStatusEvent.symbol,
                SecurityStatusEvent.status,
                SecurityStatusEvent.trade_date,
            ).where(
                SecurityStatusEvent.symbol.in_(symbol_set),
                SecurityStatusEvent.trade_date <= trade_date,
            )
        ).all()

        by_symbol: dict[str, tuple[date, str]] = {}
        for symbol, status, observed_date in rows:
            current = by_symbol.get(symbol)
            if current is None or observed_date > current[0]:
                by_symbol[symbol] = (observed_date, status)
        return {symbol: value[1] for symbol, value in by_symbol.items()}

    def status_on_date(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
    ) -> str | None:
        return self.statuses_on_date(db, symbols={symbol}, trade_date=trade_date).get(symbol)

    def record_status(
        self,
        db: Session,
        *,
        symbol: str,
        trade_date: date,
        status: str,
        source_name: str,
        source_type: str | None = None,
        batch_id: int | None = None,
        raw_payload: dict[str, Any] | None = None,
        effective_from: date | None = None,
        effective_to: date | None = None,
        note: str | None = None,
    ) -> SecurityStatusEvent:
        """Idempotently record a daily status observation.

        Duplicate ``symbol + trade_date + status + source_name`` rows are
        ignored by the unique constraint; callers that need to replace a prior
        observation should use a manual correction workflow.
        """
        if status not in self.ALLOWED_STATUSES:
            raise ValueError(f"invalid status {status!r}")
        row = SecurityStatusEvent(
            symbol=symbol,
            trade_date=trade_date,
            status=status,
            source_name=source_name,
            source_type=source_type,
            batch_id=batch_id,
            raw_payload=raw_payload,
            effective_from=effective_from,
            effective_to=effective_to,
            note=note,
        )
        db.add(row)
        db.flush()
        return row

    def suspended_symbols_on_date(
        self,
        db: Session,
        *,
        symbols: set[str] | list[str],
        trade_date: date,
    ) -> set[str]:
        """Return the subset of symbols that are suspended on ``trade_date``."""
        statuses = self.statuses_on_date(db, symbols=symbols, trade_date=trade_date)
        return {symbol for symbol, status in statuses.items() if status == "suspended"}
