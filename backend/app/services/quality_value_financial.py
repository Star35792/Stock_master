"""Exact cash/profit evidence from curated, single-symbol CNY statements.

Unavailable or conflicting inputs produce no metric. No single-period ROE,
deducted earnings or total-liability proxy is promoted to strategy evidence.
"""

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.strategies.quality_value_attention import Evidence

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _day(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(SHANGHAI).date() if value.tzinfo else None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            if len(value) == 10:
                return date.fromisoformat(value)
            return _day(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None
    return None


def financial_metrics(rows: list[dict], as_of: date) -> dict[str, Evidence]:
    """Return only fully supported three-year totals and operating-cash TTM.

    Dates of collection/revision must be explicit; naive timestamps cannot
    certify the Shanghai availability date. Annual windows never slide back
    to conceal a missing latest completed year.
    """
    symbols = {str(r.get("symbol", "")).strip().upper() for r in rows}
    if len(symbols) > 1 or "" in symbols:
        raise ValueError("financial_metrics requires one nonblank symbol")
    # key -> [(value, known_on, fundamental source reference)]
    observations: dict[tuple[str, date], list[tuple[Decimal, date, str]]] = {}
    for row in rows:
        kind = row.get("statement_type")
        if kind not in {"income", "cash_flow"}:
            continue
        end = _day(row.get("period_end"))
        report = _day(row.get("report_date"))
        created = _day(row.get("created_at"))
        updated = _day(row.get("updated_at"))
        if (row.get("currency") != "CNY" or not row.get("id")
                or not str(row.get("source_name", "")).strip()
                or any(d is None for d in (end, report, created, updated))):
            continue
        assert end is not None and report is not None
        assert created is not None and updated is not None
        if (end > report or max(report, created, updated) > as_of
                or (end.month, end.day) not in {(3, 31), (6, 30), (9, 30), (12, 31)}):
            continue
        metrics = row.get("metrics")
        try:
            if isinstance(metrics, str):
                metrics = json.loads(metrics, parse_float=Decimal, parse_int=Decimal)
            if not isinstance(metrics, dict):
                continue
            raw = metrics.get("net_profit" if kind == "income" else "act_cash_flow_net")
            if raw is None or isinstance(raw, (bool, float)):
                continue
            value = Decimal(raw)
            if not value.is_finite():
                continue
        except (ValueError, TypeError, InvalidOperation):
            continue
        key = (kind, end)
        observations.setdefault(key, []).append((
            value, max(report, created, updated),
            f"fundamental.financial_statement:{row['id']}:{row['source_name']}",
        ))
    usable = {key: entries for key, entries in observations.items()
              if len({e[0] for e in entries}) == 1}
    result: dict[str, Evidence] = {}

    def emit(name: str, value: Decimal, keys: list[tuple[str, date]]) -> None:
        entries = [entry for key in keys for entry in usable[key]]
        result[name] = Evidence(
            value=value, source_ref=json.dumps(sorted({e[2] for e in entries})),
            known_on=max(e[1] for e in entries), valid_through=as_of, trusted=True,
        )

    def total(keys: list[tuple[str, date]]) -> Decimal:
        return sum((usable[key][0][0] for key in keys), Decimal(0))

    years = [as_of.year - offset for offset in (1, 2, 3)]
    income = [("income", date(year, 12, 31)) for year in years]
    cash = [("cash_flow", date(year, 12, 31)) for year in years]
    if all(key in usable for key in income):
        profit = total(income)
        emit("net_income_sum_3y", profit, income)
        if profit > 0 and all(key in usable for key in cash):
            emit("ocf_profit_3y", total(cash) / profit, income + cash)

    annual = ("cash_flow", date(as_of.year - 1, 12, 31))
    current = sorted((key for key in observations
                      if key[0] == "cash_flow" and key[1].year == as_of.year
                      and key[1].month < 12), key=lambda key: key[1])
    # Do not call an old annual cash flow "TTM" once a current-year report
    # is required. These inclusive cutoffs are an explicit screening policy;
    # they do not infer that a particular company has already filed.
    required_month = 0
    for month, day, report_month in ((4, 30, 3), (8, 31, 6), (10, 31, 9)):
        if as_of >= date(as_of.year, month, day):
            required_month = report_month
    if annual in usable:
        if not current and required_month == 0:
            emit("ocf_ttm", total([annual]), [annual])
        elif current:
            latest = current[-1]
            prior = ("cash_flow", latest[1].replace(year=as_of.year - 1))
            if (latest[1].month >= required_month
                    and latest in usable and prior in usable):
                emit("ocf_ttm", total([annual, latest]) - total([prior]),
                     [annual, latest, prior])
    return result
