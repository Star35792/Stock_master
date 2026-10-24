from datetime import date

import pytest

from app.backtest.rebalance import select_rebalance_dates


def _dates(*iso_dates: str) -> list[date]:
    return [date.fromisoformat(value) for value in iso_dates]


def test_daily_rule_keeps_every_open_date() -> None:
    dates = _dates("2026-07-01", "2026-07-02", "2026-07-03")
    assert select_rebalance_dates(dates, rule="daily") == set(dates)


def test_weekly_rule_selects_last_open_date_of_each_iso_week() -> None:
    # 2026-06-29..07-03 is ISO week 27; 07-06..07-10 is week 28.
    dates = _dates(
        "2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03",
        "2026-07-06", "2026-07-07", "2026-07-08",
    )
    assert select_rebalance_dates(dates, rule="weekly") == {
        date(2026, 7, 3),
        date(2026, 7, 8),
    }


def test_monthly_rule_selects_last_open_date_of_each_month() -> None:
    dates = _dates("2026-07-30", "2026-07-31", "2026-08-01", "2026-08-03")
    assert select_rebalance_dates(dates, rule="monthly") == {
        date(2026, 7, 31),
        date(2026, 8, 3),
    }


def test_every_n_trading_days_takes_every_nth_open_date() -> None:
    dates = _dates("2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06", "2026-07-07")
    assert select_rebalance_dates(dates, rule="every_n_trading_days", interval=20) == {
        date(2026, 7, 1),
    }
    assert select_rebalance_dates(dates, rule="every_n_trading_days", interval=3) == {
        date(2026, 7, 1),
        date(2026, 7, 6),
    }


def test_unsorted_and_duplicate_input_is_normalized() -> None:
    dates = _dates("2026-07-03", "2026-07-01", "2026-07-01", "2026-07-02")
    assert select_rebalance_dates(dates, rule="daily") == {
        date(2026, 7, 1),
        date(2026, 7, 2),
        date(2026, 7, 3),
    }


def test_empty_open_dates_returns_empty_set() -> None:
    assert select_rebalance_dates([], rule="weekly") == set()


def test_invalid_rule_or_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported rebalance_rule"):
        select_rebalance_dates(_dates("2026-07-01"), rule="quarterly")
    with pytest.raises(ValueError, match="rebalance_interval"):
        select_rebalance_dates(_dates("2026-07-01"), rule="daily", interval=0)
