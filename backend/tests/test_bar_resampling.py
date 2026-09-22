from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.bar_resampling import DailyBarResampler


def _bar(
    trade_date: date,
    *,
    open_price: str,
    high: str,
    low: str,
    close: str,
    volume: str | None,
    amount: str | None,
):
    return SimpleNamespace(
        symbol="600000.SH",
        security_type="stock",
        trade_date=trade_date,
        adjust_type="qfq",
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=None if volume is None else Decimal(volume),
        amount=None if amount is None else Decimal(amount),
        quality_status="trusted",
        is_suspect=False,
    )


def test_resampler_aggregates_complete_week_without_float_conversion() -> None:
    open_dates = [date(2026, 9, day) for day in range(14, 19)]
    bars = [
        _bar(
            trade_date,
            open_price=str(10 + index),
            high=str(11 + index),
            low=str(9 + index),
            close=str(10.5 + index),
            volume="100",
            amount="1000",
        )
        for index, trade_date in enumerate(open_dates)
    ]

    result = DailyBarResampler().resample(
        bars=bars,
        open_dates=open_dates,
        frequency="weekly",
    )

    assert len(result) == 1
    assert result[0].period_start_date == date(2026, 9, 14)
    assert result[0].period_end_date == date(2026, 9, 18)
    assert result[0].calendar_period_end_date == date(2026, 9, 20)
    assert result[0].first_trade_date == date(2026, 9, 14)
    assert result[0].last_trade_date == date(2026, 9, 18)
    assert result[0].open == Decimal("10")
    assert result[0].high == Decimal("15")
    assert result[0].low == Decimal("9")
    assert result[0].close == Decimal("14.5")
    assert result[0].volume == Decimal("500")
    assert result[0].amount == Decimal("5000")
    assert result[0].is_complete is True


def test_resampler_excludes_incomplete_period_by_default() -> None:
    open_dates = [date(2026, 9, day) for day in range(14, 19)]
    bars = [
        _bar(
            date(2026, 9, 16),
            open_price="10",
            high="11",
            low="9",
            close="10.5",
            volume="100",
            amount="1000",
        )
    ]

    assert (
        DailyBarResampler().resample(
            bars=bars,
            open_dates=open_dates,
            frequency="weekly",
        )
        == []
    )

    included = DailyBarResampler().resample(
        bars=bars,
        open_dates=open_dates,
        frequency="weekly",
        include_incomplete=True,
    )
    assert included[0].is_complete is False
    assert included[0].source_bar_count == 1
    assert included[0].expected_open_count == 5
    assert included[0].missing_bar_count == 4


def test_resampler_does_not_publish_partial_volume_or_amount_totals() -> None:
    open_dates = [date(2026, 9, 1), date(2026, 9, 2)]
    bars = [
        _bar(
            open_dates[0],
            open_price="10",
            high="11",
            low="9",
            close="10.5",
            volume="100",
            amount="1000",
        ),
        _bar(
            open_dates[1],
            open_price="10.5",
            high="12",
            low="10",
            close="11",
            volume=None,
            amount=None,
        ),
    ]

    result = DailyBarResampler().resample(
        bars=bars,
        open_dates=open_dates,
        frequency="monthly",
    )

    assert result[0].volume is None
    assert result[0].amount is None
    assert result[0].is_complete is True


def test_resampler_extends_calendar_query_to_full_period_boundaries() -> None:
    assert DailyBarResampler.query_bounds(
        start_date=date(2026, 9, 16),
        end_date=date(2026, 10, 8),
        frequency="monthly",
    ) == (date(2026, 9, 1), date(2026, 10, 31))


def test_resampler_rejects_non_consumable_inputs() -> None:
    bar = _bar(
        date(2026, 9, 14),
        open_price="10",
        high="11",
        low="9",
        close="10.5",
        volume="100",
        amount="1000",
    )
    bar.quality_status = "suspect"
    bar.is_suspect = True

    with pytest.raises(ValueError, match="consumable"):
        DailyBarResampler().resample(
            bars=[bar],
            open_dates=[bar.trade_date],
            frequency="weekly",
        )


def test_resampler_keeps_iso_week_together_across_calendar_year() -> None:
    open_dates = [
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 2),
    ]
    bars = [
        _bar(
            trade_date,
            open_price="10",
            high="11",
            low="9",
            close="10.5",
            volume="100",
            amount="1000",
        )
        for trade_date in open_dates
    ]

    result = DailyBarResampler().resample(
        bars=bars,
        open_dates=open_dates,
        frequency="weekly",
    )

    assert len(result) == 1
    assert result[0].period_start_date == date(2025, 12, 29)
    assert result[0].period_end_date == date(2026, 1, 2)
