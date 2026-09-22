from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

ResampleFrequency = Literal["weekly", "monthly"]


@dataclass(frozen=True)
class ResampledBar:
    symbol: str
    security_type: str
    frequency: ResampleFrequency
    period_start_date: date
    period_end_date: date
    calendar_period_start_date: date
    calendar_period_end_date: date
    first_trade_date: date
    last_trade_date: date
    adjust_type: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None
    amount: Decimal | None
    source_bar_count: int
    expected_open_count: int
    missing_bar_count: int
    is_complete: bool
    quality_status: Literal["trusted", "manual_fixed"]
    input_quality_scope: Literal["consumable"] = "consumable"


class DailyBarResampler:
    """Aggregate one trusted daily series; open_dates must cover full period bounds."""

    def resample(
        self,
        *,
        bars: list[Any],
        open_dates: list[date],
        frequency: ResampleFrequency,
        include_incomplete: bool = False,
    ) -> list[ResampledBar]:
        if frequency not in {"weekly", "monthly"}:
            raise ValueError("frequency must be one of: weekly, monthly")
        if not bars:
            return []

        ordered_bars = sorted(bars, key=lambda bar: bar.trade_date)
        symbols = {bar.symbol for bar in ordered_bars}
        adjust_types = {bar.adjust_type for bar in ordered_bars}
        if len(symbols) != 1 or len(adjust_types) != 1:
            raise ValueError("resampling requires one symbol and one adjust_type")
        if any(
            getattr(bar, "quality_status", None) not in {"trusted", "manual_fixed"}
            or getattr(bar, "is_suspect", False)
            for bar in ordered_bars
        ):
            raise ValueError("resampling requires consumable daily bars")
        trade_dates = [bar.trade_date for bar in ordered_bars]
        if len(trade_dates) != len(set(trade_dates)):
            raise ValueError("daily bars must contain unique trade dates")

        bars_by_period: dict[tuple[int, int], list[Any]] = defaultdict(list)
        expected_by_period: dict[tuple[int, int], set[date]] = defaultdict(set)
        for bar in ordered_bars:
            bars_by_period[self._period_key(bar.trade_date, frequency)].append(bar)
        for open_date in sorted(set(open_dates)):
            expected_by_period[self._period_key(open_date, frequency)].add(open_date)

        rows = []
        for period_key in sorted(bars_by_period):
            period_bars = bars_by_period[period_key]
            actual_dates = {bar.trade_date for bar in period_bars}
            expected_dates = expected_by_period.get(period_key, set())
            is_complete = bool(expected_dates) and actual_dates == expected_dates
            if not include_incomplete and not is_complete:
                continue
            calendar_period_start, calendar_period_end = self._period_bounds(
                period_bars[0].trade_date,
                frequency,
            )
            period_start = min(expected_dates) if expected_dates else min(actual_dates)
            period_end = max(expected_dates) if expected_dates else max(actual_dates)
            source_quality_statuses = {
                getattr(bar, "quality_status", "trusted") for bar in period_bars
            }
            rows.append(
                ResampledBar(
                    symbol=period_bars[0].symbol,
                    security_type=period_bars[0].security_type,
                    frequency=frequency,
                    period_start_date=period_start,
                    period_end_date=period_end,
                    calendar_period_start_date=calendar_period_start,
                    calendar_period_end_date=calendar_period_end,
                    first_trade_date=period_bars[0].trade_date,
                    last_trade_date=period_bars[-1].trade_date,
                    adjust_type=period_bars[0].adjust_type,
                    open=Decimal(period_bars[0].open),
                    high=max(Decimal(bar.high) for bar in period_bars),
                    low=min(Decimal(bar.low) for bar in period_bars),
                    close=Decimal(period_bars[-1].close),
                    volume=self._sum_complete(period_bars, "volume"),
                    amount=self._sum_complete(period_bars, "amount"),
                    source_bar_count=len(period_bars),
                    expected_open_count=len(expected_dates),
                    missing_bar_count=len(expected_dates - actual_dates),
                    is_complete=is_complete,
                    quality_status=(
                        "manual_fixed"
                        if "manual_fixed" in source_quality_statuses
                        else "trusted"
                    ),
                    input_quality_scope="consumable",
                )
            )
        return rows

    @classmethod
    def query_bounds(
        cls,
        *,
        start_date: date,
        end_date: date,
        frequency: ResampleFrequency,
    ) -> tuple[date, date]:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        period_start, _ = cls._period_bounds(start_date, frequency)
        _, period_end = cls._period_bounds(end_date, frequency)
        return period_start, period_end

    @staticmethod
    def _sum_complete(bars: list[Any], field_name: str) -> Decimal | None:
        values = [getattr(bar, field_name) for bar in bars]
        if any(value is None for value in values):
            return None
        return sum((Decimal(value) for value in values), Decimal())

    @staticmethod
    def _period_key(value: date, frequency: ResampleFrequency) -> tuple[int, int]:
        if frequency == "weekly":
            iso_year, iso_week, _ = value.isocalendar()
            return iso_year, iso_week
        if frequency == "monthly":
            return value.year, value.month
        raise ValueError("frequency must be one of: weekly, monthly")

    @staticmethod
    def _period_bounds(
        value: date,
        frequency: ResampleFrequency,
    ) -> tuple[date, date]:
        if frequency == "weekly":
            start = value - timedelta(days=value.weekday())
            return start, start + timedelta(days=6)
        if frequency == "monthly":
            start = value.replace(day=1)
            next_month = (
                date(start.year + 1, 1, 1)
                if start.month == 12
                else date(start.year, start.month + 1, 1)
            )
            return start, next_month - timedelta(days=1)
        raise ValueError("frequency must be one of: weekly, monthly")
