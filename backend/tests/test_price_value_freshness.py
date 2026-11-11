from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.price_value_freshness import report_freshness

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _calendar(start: date, end: date, *, holidays: set[date] | None = None) -> list[dict]:
    rows = []
    day = start
    while day <= end:
        for exchange in ("SH", "SZ", "BJ"):
            rows.append({
                "exchange": exchange,
                "trade_date": day,
                "is_open": day.weekday() < 5 and day not in (holidays or set()),
            })
        day += timedelta(days=1)
    return rows


@pytest.mark.parametrize(
    ("target", "status", "reasons"),
    [
        ("2026-10-28", "current", []),
        ("2026-09-24", "stale", ["report_stale"]),
        ("2026-10-29", "future", ["report_date_in_future"]),
        ("2026-10-30", "future", ["report_date_in_future"]),
        (None, "unknown", ["report_date_missing"]),
        ("invalid", "unknown", ["report_date_missing"]),
    ],
)
def test_report_compares_with_completed_previous_session(target, status, reasons):
    result = report_freshness(
        target_trade_date=target,
        now=datetime(2026, 10, 29, 23, 59, tzinfo=SHANGHAI),
        calendar=_calendar(date(2026, 10, 28), date(2026, 10, 29)),
    )
    assert result["status"] == status
    assert result["reason_codes"] == reasons
    assert result["expected_trade_date"] == "2026-10-28"


def test_midnight_and_timezone_follow_shanghai_date():
    calendar = _calendar(date(2026, 10, 28), date(2026, 10, 30))
    before = report_freshness(
        target_trade_date="2026-10-28",
        now=datetime(2026, 10, 29, 15, 59, tzinfo=UTC),
        calendar=calendar,
    )
    after = report_freshness(
        target_trade_date="2026-10-28",
        now=datetime(2026, 10, 29, 16, 0, tzinfo=UTC),
        calendar=calendar,
    )
    assert before["status"] == "current"
    assert after["status"] == "stale"
    assert after["expected_trade_date"] == "2026-10-29"
    assert after["checked_at"] == "2026-10-30T00:00:00+08:00"


def test_weekend_does_not_expire_friday_report():
    result = report_freshness(
        target_trade_date="2026-10-23",
        now=datetime(2026, 10, 26, 20, tzinfo=SHANGHAI),
        calendar=_calendar(date(2026, 10, 23), date(2026, 10, 26)),
    )
    assert result["status"] == "current"


def test_long_holiday_does_not_use_natural_day_cutoff():
    holidays = {date(2026, 10, day) for day in range(1, 8)}
    result = report_freshness(
        target_trade_date="2026-09-30",
        now=datetime(2026, 10, 8, 23, tzinfo=SHANGHAI),
        calendar=_calendar(date(2026, 9, 30), date(2026, 10, 8), holidays=holidays),
    )
    assert result["status"] == "current"
    assert result["expected_trade_date"] == "2026-09-30"


@pytest.mark.parametrize("problem", ["today_missing", "exchange_missing", "gap", "conflict",
                                    "exchange_conflict", "invalid_bool", "no_open_day"])
def test_incomplete_or_conflicting_calendar_cannot_certify_current(problem):
    calendar = _calendar(date(2026, 10, 23), date(2026, 10, 26))
    if problem == "today_missing":
        calendar = [row for row in calendar if row["trade_date"] != date(2026, 10, 26)]
    elif problem == "exchange_missing":
        calendar = calendar[:-1]
    elif problem == "gap":
        calendar = [row for row in calendar if row["trade_date"] != date(2026, 10, 25)]
    elif problem == "conflict":
        calendar.append({"exchange": "SH", "trade_date": date(2026, 10, 26), "is_open": False})
    elif problem == "exchange_conflict":
        calendar[-1]["is_open"] = False
    elif problem == "invalid_bool":
        calendar[-1]["is_open"] = "false"
    else:
        calendar = [row for row in calendar if row["trade_date"] > date(2026, 10, 23)]
    result = report_freshness(
        target_trade_date="2026-10-23",
        now=datetime(2026, 10, 26, tzinfo=SHANGHAI),
        calendar=calendar,
    )
    assert result["status"] == "unknown"
    assert result["expected_trade_date"] is None
    assert result["reason_codes"] == ["calendar_coverage_incomplete"]


def test_duplicate_identical_calendar_and_string_dates_are_supported():
    calendar = _calendar(date(2026, 10, 28), date(2026, 10, 29))
    calendar += [dict(row) for row in calendar]
    calendar = [{**row, "trade_date": row["trade_date"].isoformat()} for row in calendar]
    original = [dict(row) for row in calendar]
    result = report_freshness(
        target_trade_date="2026-10-28",
        now=datetime(2026, 10, 29, tzinfo=SHANGHAI),
        calendar=calendar,
    )
    assert result["status"] == "current"
    assert calendar == original


def test_empty_calendar_and_absent_date_report_both_missing_evidence():
    result = report_freshness(
        target_trade_date=None,
        now=datetime(2026, 10, 29, tzinfo=SHANGHAI),
        calendar=[],
    )
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["report_date_missing", "calendar_coverage_incomplete"]


def test_naive_clock_is_rejected():
    with pytest.raises(ValueError, match="timezone"):
        report_freshness(target_trade_date="2026-10-28", now=datetime(2026, 10, 29), calendar=[])
