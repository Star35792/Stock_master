from bisect import bisect_right
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import SecurityIndustryHistory


@dataclass(frozen=True)
class IndustryInterval:
    """一个标的的一条行业生效区间。"""

    effective_from: date
    effective_to: date | None
    industry: str


def resolve_industries_by_date(
    intervals: dict[str, list[IndustryInterval]],
    dates: list[date],
    *,
    fallback: dict[str, str] | None = None,
) -> dict[date, dict[str, str]]:
    """把区间表解析成「每个交易日一组行业标签」。

    为什么要有这个批量入口
    ----------------------
    :meth:`SecurityIndustryHistoryRepository.industries_as_of` 每查一个交易日
    就发一次 SQL（带 5,585 个 symbol 的 IN 子句）。两年回测约 500 个交易日，
    就是 500 次全表扫描——慢，而且没法给「一次拿全、内存解析」的调用方用。

    这里一次把区间读进内存，按标的排好序，之后每个交易日用二分查找定位
    生效区间。区间之间不重叠（写入时保证），所以二分是安全的。

    ``fallback`` 是当前标签回退：没有区间的标的用它。**回退是显式传入的**，
    调用方必须自己决定要不要回退——这是 PIT 铁律，回退必须留痕。
    """

    starts: dict[str, list[date]] = {}
    ordered_rows: dict[str, list[IndustryInterval]] = {}
    for symbol, rows in intervals.items():
        ordered = sorted(rows, key=lambda row: row.effective_from)
        ordered_rows[symbol] = ordered
        starts[symbol] = [row.effective_from for row in ordered]

    result: dict[date, dict[str, str]] = {}
    for as_of in dates:
        resolved: dict[str, str] = {}
        for symbol, row_starts in starts.items():
            index = bisect_right(row_starts, as_of) - 1
            if index < 0:
                continue
            row = ordered_rows[symbol][index]
            if row.effective_to is not None and row.effective_to <= as_of:
                continue
            resolved[symbol] = row.industry
        if fallback:
            for symbol, industry in fallback.items():
                resolved.setdefault(symbol, industry)
        result[as_of] = resolved
    return result


class SecurityIndustryHistoryRepository:
    """Point-in-time industry classification lookups.

    ``industry_as_of`` resolves the label effective on a given trade date:
    the row whose ``effective_from <= as_of`` and whose ``effective_to`` is
    either NULL or strictly greater than ``as_of``. Symbols without history
    are absent from the result; callers must decide explicitly between
    falling back to the current label and failing closed.
    """

    def load_intervals(
        self,
        db: Session,
        *,
        symbols: set[str] | list[str],
    ) -> dict[str, list[IndustryInterval]]:
        """一次取回全部生效区间。

        供 :func:`resolve_industries_by_date` 在内存里解析，避免逐日查库。
        """

        symbol_set = {symbol for symbol in symbols if symbol}
        if not symbol_set:
            return {}
        rows = db.execute(
            select(
                SecurityIndustryHistory.symbol,
                SecurityIndustryHistory.industry,
                SecurityIndustryHistory.effective_from,
                SecurityIndustryHistory.effective_to,
            ).where(SecurityIndustryHistory.symbol.in_(symbol_set))
        ).all()
        intervals: dict[str, list[IndustryInterval]] = {}
        for symbol, industry, effective_from, effective_to in rows:
            intervals.setdefault(symbol, []).append(
                IndustryInterval(
                    effective_from=effective_from,
                    effective_to=effective_to,
                    industry=industry,
                )
            )
        return intervals

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
