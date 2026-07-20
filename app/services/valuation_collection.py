from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.data_sources import HithinkFinanceDataSource
from app.repositories.market import ValuationSnapshotRepository

_SHANGHAI = ZoneInfo("Asia/Shanghai")

_TOTAL_MARKET_CAP = ("total_market_cap", "market_cap", "total_mv", "total_value")
_FLOAT_MARKET_CAP = ("float_market_cap", "circulating_market_cap", "float_mv")
_TOTAL_SHARES = ("total_shares", "total_share", "total_capital", "share_total")
_FLOAT_SHARES = (
    "float_shares",
    "float_share",
    "circulating_shares",
    "circulating_share",
)
_PE_TTM = ("pe_ttm", "pettm", "pe_ratio_ttm", "pe")
_PB = ("pb", "pb_ratio", "price_to_book")
_PS_TTM = ("ps_ttm", "psttm", "ps_ratio_ttm", "ps")
_CLOSE = ("close", "last_price", "latest_price", "price", "close_price")


@dataclass(frozen=True)
class ValuationCollectionResult:
    input_rows: int
    normalized_rows: int


class HithinkValuationCollectionService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        repository: ValuationSnapshotRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.repository = repository or ValuationSnapshotRepository()

    def collect_current(
        self,
        db: Session,
        *,
        symbols: list[str] | None = None,
        fetch_all_market: bool = False,
        limit: int = 1000,
        offset: int = 0,
    ) -> ValuationCollectionResult:
        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        items = self.data_source.fetch_price_snapshots(
            symbols=normalized_symbols,
            fetch_all_market=fetch_all_market,
            limit=limit,
            offset=offset,
        )
        rows = [
            row
            for item in items
            if (row := _normalize_snapshot(item, self.data_source.name)) is not None
        ]
        normalized_rows = self.repository.upsert_many(db, rows=rows)
        return ValuationCollectionResult(
            input_rows=len(items),
            normalized_rows=normalized_rows,
        )


def _normalize_snapshot(item: dict[str, Any], source_name: str) -> dict[str, Any] | None:
    symbol = item.get("thscode") or item.get("symbol")
    if not symbol:
        return None
    close = _first_decimal(item, *_CLOSE)
    total_shares = _first_decimal(item, *_TOTAL_SHARES)
    float_shares = _first_decimal(item, *_FLOAT_SHARES)
    total_market_cap = _first_decimal(item, *_TOTAL_MARKET_CAP)
    float_market_cap = _first_decimal(item, *_FLOAT_MARKET_CAP)
    if total_market_cap is None and close is not None and total_shares is not None:
        total_market_cap = close * total_shares
    if float_market_cap is None and close is not None and float_shares is not None:
        float_market_cap = close * float_shares

    return {
        "symbol": str(symbol).upper(),
        "trade_date": _snapshot_date(item),
        "total_market_cap": total_market_cap,
        "float_market_cap": float_market_cap,
        "total_shares": total_shares,
        "float_shares": float_shares,
        "pe_ttm": _first_decimal(item, *_PE_TTM),
        "pb": _first_decimal(item, *_PB),
        "ps_ttm": _first_decimal(item, *_PS_TTM),
        "close": close,
        "currency": str(item.get("currency") or "CNY"),
        "source_name": source_name,
        "raw_payload": item,
    }


def _snapshot_date(item: dict[str, Any]) -> date:
    if item.get("trade_date"):
        return date.fromisoformat(str(item["trade_date"]))
    timestamp = (
        item.get("date_ms")
        or item.get("trade_date_ms")
        or item.get("timestamp")
        or item.get("time_ms")
    )
    if timestamp:
        return datetime.fromtimestamp(int(timestamp) / 1000, tz=_SHANGHAI).date()
    return date.today()


def _first_decimal(item: dict[str, Any], *keys: str) -> Decimal | None:
    for key in keys:
        if item.get(key) is not None:
            return _decimal(item[key])
    return None


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
