"""Rebalance-date selection for non-daily backtests.

The engine itself is frequency-agnostic: it executes whatever dates the
strategy emits signals on. Non-daily rebalancing is therefore implemented by
filtering strategy signals down to the selected rebalance dates, computed
from the exchange open-date calendar. A signal emitted on the last trading
day of a week/month still executes on the next open date (T+1), matching the
daily path's execution semantics.
"""

from collections.abc import Sequence
from datetime import date

REBALANCE_RULES = ("daily", "weekly", "monthly", "every_n_trading_days")


def select_rebalance_dates(
    open_dates: Sequence[date],
    *,
    rule: str,
    interval: int = 1,
) -> set[date]:
    """Return the subset of ``open_dates`` on which rebalance signals survive.

    - ``daily``: every open date.
    - ``weekly``: the last open date of each ISO year/week.
    - ``monthly``: the last open date of each calendar month.
    - ``every_n_trading_days``: every ``interval``-th open date by index
      (interval=1 equals daily).
    """
    if rule not in REBALANCE_RULES:
        raise ValueError(f"unsupported rebalance_rule: {rule}")
    if interval < 1:
        raise ValueError("rebalance_interval must be >= 1")
    ordered = sorted(set(open_dates))
    if not ordered:
        return set()
    if rule == "daily":
        return set(ordered)
    if rule == "every_n_trading_days":
        return set(ordered[::interval])

    selected: set[date] = set()
    for current, following in zip(ordered, ordered[1:] + [None], strict=True):
        key = _period_key(current, rule)
        if following is None or _period_key(following, rule) != key:
            selected.add(current)
    return selected


def _period_key(day: date, rule: str) -> tuple[int, int]:
    if rule == "weekly":
        iso = day.isocalendar()
        return iso[0], iso[1]
    return day.year, day.month
