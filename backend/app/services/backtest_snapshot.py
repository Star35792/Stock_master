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


def build_benchmark_snapshot(*, bars: list[Any], equity_dates: list[date]) -> dict[str, Any]:
    """Freeze the comparable benchmark input without duplicating its full price history."""
    ordered = sorted(
        (
            bar for bar in bars
            if equity_dates and equity_dates[0] <= bar.trade_date <= equity_dates[-1]
        ),
        key=lambda bar: bar.trade_date,
    )
    dates = [bar.trade_date for bar in ordered]
    if len(equity_dates) < 2 or dates != equity_dates:
        return {
            "version": "benchmark_window_v1", "status": "unavailable",
            "reason": "trusted benchmark dates do not cover strategy equity dates",
        }
    if any(bar.close is None or bar.close <= 0 for bar in ordered):
        return {
            "version": "benchmark_window_v1", "status": "unavailable",
            "reason": "trusted benchmark requires positive closes",
        }
    content = json.dumps(
        [(bar.trade_date.isoformat(), str(bar.close)) for bar in ordered],
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "version": "benchmark_window_v1", "status": "verified",
        "start_date": dates[0].isoformat(), "end_date": dates[-1].isoformat(),
        "date_count": len(dates), "first_close": str(ordered[0].close),
        "last_close": str(ordered[-1].close),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_input_data_version(
    *, market_snapshot: dict[str, Any],
    strategy_input_snapshot: dict[str, Any] | None,
    benchmark_snapshot: dict[str, Any] | None,
) -> str:
    """Identify the exact input digests consumed by a completed research run."""
    content = json.dumps(
        {
            "market_sha256": market_snapshot["sha256"],
            "strategy_sha256": (
                strategy_input_snapshot.get("sha256")
                if strategy_input_snapshot else None
            ),
            "benchmark_sha256": (
                benchmark_snapshot.get("sha256") if benchmark_snapshot else None
            ),
            "benchmark_status": (
                benchmark_snapshot.get("status") if benchmark_snapshot else None
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"input-{hashlib.sha256(content).hexdigest()[:20]}"


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
