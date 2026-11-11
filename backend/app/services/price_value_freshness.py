"""Read-time freshness for saved research reports, without altering their snapshot."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXCHANGES = frozenset({"SH", "SZ", "BJ"})


def _date(value: object) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _expected_date(today: date, calendar: list[dict]) -> date | None:
    """Require continuous, explicit three-exchange dates through the last open day.

    Only the supplied window is traversed. Knowing a recent complete open day is
    sufficient to reject an older report, without requiring its entire history.
    The current day must be covered, but its report is due after midnight only.
    """
    states: dict[date, dict[str, set[bool]]] = {}
    invalid: set[date] = set()
    for row in calendar:
        day = _date(row.get("trade_date"))
        exchange = row.get("exchange")
        if day is None or day > today or exchange not in EXCHANGES:
            continue
        if not isinstance(row.get("is_open"), bool):
            invalid.add(day)
            continue
        states.setdefault(day, {}).setdefault(exchange, set()).add(row["is_open"])

    day = today
    # A missing date terminates the walk, so work is bounded by the input size.
    while day in states:
        exchanges = states[day]
        if day in invalid or exchanges.keys() != EXCHANGES:
            return None
        if any(len(values) != 1 for values in exchanges.values()):
            return None
        values = {next(iter(value)) for value in exchanges.values()}
        if len(values) != 1:
            return None
        if day < today and values == {True}:
            return day
        if day == date.min:
            return None
        day -= timedelta(days=1)
    return None


def report_freshness(
    *, target_trade_date: str | None, now: datetime, calendar: list[dict]
) -> dict:
    """Compare a saved target with the latest report due on the Shanghai clock.

    The midnight job produces the preceding completed trading day's report.
    Missing/contradictory calendar records cannot certify a report as current.
    No report row, saved status, or snapshot hash is mutated by this function.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("freshness clock must include timezone")
    local = now.astimezone(SHANGHAI)
    expected = _expected_date(local.date(), calendar)
    target = _date(target_trade_date)
    result = {
        "status": "unknown",
        "expected_trade_date": expected.isoformat() if expected else None,
        "checked_at": local.isoformat(),
        "reason_codes": [],
    }
    if target is None:
        result["reason_codes"].append("report_date_missing")
    if expected is None:
        result["reason_codes"].append("calendar_coverage_incomplete")
    if target is None or expected is None:
        return result
    if target < expected:
        result.update(status="stale", reason_codes=["report_stale"])
    elif target > expected:
        result.update(status="future", reason_codes=["report_date_in_future"])
    else:
        result["status"] = "current"
    return result
