"""Explainable current research checks; thresholds are not return forecasts.

The caller supplies the exact 21 exchange sessions ending at ``as_of`` and
trusted qfq observations. Missing data never becomes a passing observation.
Base universe eligibility and report freshness remain the caller's gates.
"""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.services.quality_value_financial import financial_metrics

VERSION = "value_research_diagnostics_v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _available_at(value: object, cutoff: datetime) -> bool:
    """Collection/revision requires an explicit instant, not a date or local clock."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    return (isinstance(value, datetime) and value.tzinfo is not None
            and value.utcoffset() is not None and value <= cutoff)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, (bool, float)):
        return None
    try:
        result = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return None
    return result if result.is_finite() else None


def _trend(*, symbol: str, as_of: date, sessions: list[date], prices: list[dict]) -> dict:
    result = {
        "status": "unknown", "return_20": None, "ma20_gap": None,
        "reason_codes": [], "source_refs": [],
    }
    if (len(sessions) != 21
            or any(not isinstance(day, date) or isinstance(day, datetime) for day in sessions)
            or len(set(sessions)) != 21 or max(sessions) != as_of):
        result["reason_codes"] = ["trend_calendar_incomplete"]
        return result
    expected = sorted(sessions)
    if len(prices) != 21:
        result["reason_codes"] = ["trend_history_incomplete"]
        return result
    by_date: dict[date, Decimal] = {}
    sources = []
    for row in prices:
        day = row.get("trade_date")
        close = _decimal(row.get("close"))
        if row.get("symbol") != symbol:
            result["reason_codes"] = ["trend_symbol_mismatch"]
            return result
        if row.get("adjust_type") != "qfq":
            result["reason_codes"] = ["trend_qfq_required"]
            return result
        if (not isinstance(day, date) or isinstance(day, datetime) or day not in expected
                or day in by_date):
            result["reason_codes"] = ["trend_history_incomplete"]
            return result
        if (close is None or close <= 0 or row.get("quality_status") not in {
                "trusted", "manual_fixed"}
                or row.get("is_suspect") is not False
                or row.get("is_suspended") is not False
                or not str(row.get("source_name") or "").strip()):
            result["reason_codes"] = ["trend_quote_untrusted"]
            return result
        by_date[day] = close
        sources.append(row.get("source_ref") or (
            f"market.daily_bar:{symbol}:qfq:{day}:{row['source_name']}"
        ))
    ordered = [by_date[day] for day in expected]
    return_20 = ordered[-1] / ordered[0] - 1
    ma20 = sum(ordered[1:], Decimal(0)) / 20
    gap = ordered[-1] / ma20 - 1
    status = "mixed"
    if return_20 > 0 and gap >= 0:
        status = "improving"
    elif return_20 < 0 and gap < 0:
        status = "weak"
    result.update(
        status=status, return_20=str(return_20), ma20_gap=str(gap),
        reason_codes=[] if status == "improving" else [f"trend_{status}"],
        source_refs=sorted(sources),
    )
    return result


def _quality(
    *, symbol: str, as_of: date, rows: list[dict], financial_sector: bool,
    available_at: datetime | None = None,
) -> dict:
    names = ("net_income_sum_3y", "ocf_profit_3y", "ocf_ttm")
    annual_names = ("net_income_positive_years_3y", "net_income_min_3y", "net_income_ttm")
    result = {
        "status": "unknown", **{name: None for name in names + annual_names},
        "reason_codes": [], "source_refs": [],
    }
    if financial_sector:
        result.update(status="not_applicable", reason_codes=["quality_financial_model_required"])
        return result
    if any(str(row.get("symbol", "")).strip().upper() != symbol for row in rows):
        result["reason_codes"] = ["quality_symbol_mismatch"]
        return result
    if available_at is not None:
        rows = [row for row in rows if all(
            _available_at(row.get(field), available_at)
            for field in ("created_at", "updated_at")
        )]
    metrics = financial_metrics(rows, as_of)
    failures, missing, sources = [], [], set()
    # Annual continuity and profit TTM serve the separate, versioned shortlist.
    # It does not change the original diagnostics v1 three-metric gate.
    for name in annual_names:
        evidence = metrics.get(name)
        if evidence is not None:
            result[name] = str(evidence.value)
            sources.update(json.loads(evidence.source_ref))
    for name in names:
        evidence = metrics.get(name)
        if evidence is None:
            missing.append(f"quality_{name}_missing")
            continue
        value = evidence.value
        result[name] = str(value)
        sources.update(json.loads(evidence.source_ref))
        if (name == "ocf_profit_3y" and value < 1) or (name != "ocf_profit_3y" and value <= 0):
            failures.append(f"quality_{name}_insufficient")
    result.update(
        status="risk" if failures else "unknown" if missing else "supported",
        reason_codes=failures + missing, source_refs=sorted(sources),
    )
    return result


def diagnose(
    *, symbol: str, as_of: date, sessions: list[date], prices: list[dict],
    financial_rows: list[dict], financial_sector: bool = False,
    financial_as_of: date | None = None,
    financial_available_at: datetime | None = None,
) -> dict:
    """Return research diagnostics, never an automatic allocation or buy signal.

    Prices retain ``as_of``. Explicit current research may use a separate
    financial date and actual analysis instant; this does not certify that
    financial evidence was available on the price date. Omitted arguments
    retain the existing date-only policy.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("diagnostics require a nonblank symbol")
    financial_date = as_of if financial_as_of is None else financial_as_of
    if not isinstance(financial_date, date) or isinstance(financial_date, datetime):
        raise ValueError("financial_as_of must be a date")
    if financial_available_at is not None:
        if (not isinstance(financial_available_at, datetime)
                or financial_available_at.tzinfo is None
                or financial_available_at.utcoffset() is None):
            raise ValueError("financial_available_at must include timezone")
        if financial_date > financial_available_at.astimezone(SHANGHAI).date():
            raise ValueError("financial_as_of is after the actual availability cutoff")
    trend = _trend(symbol=symbol, as_of=as_of, sessions=sessions, prices=prices)
    quality = _quality(
        symbol=symbol, as_of=financial_date, rows=financial_rows,
        financial_sector=financial_sector, available_at=financial_available_at,
    )
    return {
        "version": VERSION, "trend": trend, "quality": quality,
        "review_eligible": trend["status"] == "improving" and quality["status"] == "supported",
    }
