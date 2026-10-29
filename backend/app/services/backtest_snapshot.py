"""Deterministic trusted-market input snapshots shared by backtest runs."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from typing import Any

_DATA_SNAPSHOT_VERSION = "trusted_market_slice_v1"
_EXECUTION_ADJUST_TYPE = "none"
_BAR_SNAPSHOT_FIELDS = (
    "symbol",
    "trade_date",
    "security_type",
    "adjust_type",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "limit_up",
    "limit_down",
    "is_suspended",
    "special_treatment_status",
    "source_name",
    "source_count",
    "quality_score",
    "quality_status",
    "is_suspect",
    "selected_raw_id",
)
_ACTION_SNAPSHOT_FIELDS = (
    "symbol",
    "ex_date",
    "action_type",
    "cash_dividend",
    "bonus_share_ratio",
    "transfer_share_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
    "split_ratio",
    "record_date",
    "payment_date",
    "source_name",
    "source_event_id",
)


def _build_market_snapshot(
    *,
    bars: list[Any],
    corporate_actions: list[Any],
    execution_calendar: dict[str, list[date]],
    start_date: date,
    end_date: date,
    symbols: list[str],
) -> dict[str, Any]:
    bar_records = [_snapshot_record(row, _BAR_SNAPSHOT_FIELDS) for row in bars]
    action_records = [
        _snapshot_record(row, _ACTION_SNAPSHOT_FIELDS) for row in corporate_actions
    ]
    bar_records.sort(key=_snapshot_sort_key)
    action_records.sort(key=_snapshot_sort_key)
    calendar_records = [
        {
            "symbol": symbol,
            "trade_dates": [trade_date.isoformat() for trade_date in dates],
        }
        for symbol, dates in sorted(execution_calendar.items())
    ]
    content = json.dumps(
        {
            "bars": bar_records,
            "corporate_actions": action_records,
            "execution_calendar": calendar_records,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "snapshot_version": _DATA_SNAPSHOT_VERSION,
        "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
        "quality_status": "trusted",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": list(symbols),
        "bar_count": len(bar_records),
        "corporate_action_count": len(action_records),
        "calendar_session_count": sum(
            len(record["trade_dates"]) for record in calendar_records
        ),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _snapshot_record(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: _snapshot_value(getattr(row, field, None))
        for field in fields
    }


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _snapshot_sort_key(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
