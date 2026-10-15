from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import SecurityIndustryHistory


class SecurityIndustryHistoryRepository:
    """Point-in-time industry classification lookups.

    ``industry_as_of`` resolves the label effective on a given trade date:
    the row whose ``effective_from <= as_of`` and whose ``effective_to`` is
    either NULL or strictly greater than ``as_of``. Symbols without history
    are absent from the result; callers must decide explicitly between
    falling back to the current label and failing closed.
    """

    def industries_as_of(
        self,
        db: Session,
        *,
        symbols: set[str] | list[str],
        as_of: date,
    ) -> dict[str, str]:
        symbol_set = {symbol for symbol in symbols if symbol}
        if not symbol_set:
            return {}
        rows_with_from = db.execute(
            select(
                SecurityIndustryHistory.symbol,
                SecurityIndustryHistory.industry,
                SecurityIndustryHistory.effective_from,
            ).where(
                SecurityIndustryHistory.symbol.in_(symbol_set),
                SecurityIndustryHistory.effective_from <= as_of,
                (
                    SecurityIndustryHistory.effective_to.is_(None)
                    | (SecurityIndustryHistory.effective_to > as_of)
                ),
            )
        ).all()
        # An overlapping-window data bug could yield multiple labels for one
        # symbol; keep the latest ``effective_from`` deterministically instead
        # of silently dropping rows.
        by_symbol: dict[str, tuple[date, str]] = {}
        for symbol, industry, effective_from in rows_with_from:
            current = by_symbol.get(symbol)
            if current is None or effective_from >= current[0]:
                by_symbol[symbol] = (effective_from, industry)
        return {symbol: value[1] for symbol, value in by_symbol.items()}

    def industry_as_of(
        self,
        db: Session,
        *,
        symbol: str,
        as_of: date,
    ) -> str | None:
        return self.industries_as_of(db, symbols={symbol}, as_of=as_of).get(symbol)

    def record_interval(
        self,
        db: Session,
        *,
        symbol: str,
        industry: str,
        effective_from: date,
        effective_to: date | None,
        source_name: str,
    ) -> SecurityIndustryHistory:
        """Close any open interval and append the new label window."""

        open_rows = db.scalars(
            select(SecurityIndustryHistory).where(
                SecurityIndustryHistory.symbol == symbol,
                SecurityIndustryHistory.effective_to.is_(None),
            )
        ).all()
        for row in open_rows:
            if effective_from > row.effective_from:
                row.effective_to = effective_from
            else:
                # A backfill that starts before the open interval would make it
                # unreachable; refuse instead of corrupting the timeline.
                raise ValueError(
                    "industry history interval must start after the open "
                    f"interval for {symbol}: open from {row.effective_from}, "
                    f"requested from {effective_from}"
                )
        row = SecurityIndustryHistory(
            symbol=symbol,
            industry=industry,
            effective_from=effective_from,
            effective_to=effective_to,
            source_name=source_name,
        )
        db.add(row)
        db.flush()
        return row
