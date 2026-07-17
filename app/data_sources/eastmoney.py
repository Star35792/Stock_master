from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError, loads
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config.settings import settings
from app.data_sources.base import DailyBarRecord

QUOTE_HOST = "https://push2delay.eastmoney.com"
KLINE_HOST = "https://push2his.eastmoney.com"
KLINE_FALLBACK_HOST = "https://push2.eastmoney.com"
_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "https://data.eastmoney.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


class EastmoneyDataError(RuntimeError):
    pass


class EastmoneyDataSource:
    """Small, dependency-free client for Eastmoney's public delayed endpoints."""

    name = "eastmoney"
    source_type = "api"

    def __init__(
        self,
        *,
        timeout_seconds: int | None = None,
        retries: int | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds or settings.eastmoney_timeout_seconds
        self.retries = retries if retries is not None else settings.eastmoney_retries

    def fetch_main_fund_rank(self, *, limit: int = 100, market: str = "all") -> list[dict]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        market_filters = {
            "all": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23,m:1+t:8",
            "sh": "m:0+t:6,m:0+t:13,m:0+t:80",
            "sz": "m:1+t:2,m:1+t:23,m:1+t:8",
            "cyb": "m:0+t:80",
            "kcb": "m:1+t:23",
        }
        if market not in market_filters:
            raise ValueError(f"unsupported market: {market}")
        data = self._get_json(
            QUOTE_HOST,
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f62",
                "fs": market_filters[market],
                "fields": "f12,f14,f2,f3,f62,f184,f66,f70,f76,f78",
            },
        )
        return (data.get("data") or {}).get("diff") or []

    def fetch_sector_fund_flow(self, *, kind: str = "industry", limit: int = 100) -> list[dict]:
        if kind not in {"industry", "concept"}:
            raise ValueError("kind must be industry or concept")
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        data = self._get_json(
            QUOTE_HOST,
            "/api/qt/clist/get",
            {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f62",
                "fs": "m:90+t:2" if kind == "industry" else "m:90+t:3",
                "fields": "f12,f14,f2,f3,f62,f184,f128,f136",
            },
        )
        return (data.get("data") or {}).get("diff") or []

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date | None = None,
        end_date: date | None = None,
        count: int = 5000,
        adjust_type: str = "qfq",
    ) -> list[DailyBarRecord]:
        if adjust_type != "qfq":
            raise ValueError("Eastmoney K-lines currently support qfq only")
        code = symbol.strip().upper()
        if "." in code:
            code = code.split(".", 1)[0]
        if not code.isdigit() or len(code) != 6:
            raise ValueError("symbol must contain a six-digit A-share code")
        if count < 1 or count > 5000:
            raise ValueError("count must be between 1 and 5000")
        params = {
            "secid": f"{market_prefix(code)}.{code}",
            "ut": "fa5fd1943c7b386f172d6893dbfba10b",
            "klt": 101,
            "fqt": 1,
            "beg": 0,
            "end": "20500101",
            "lmt": count,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
        last_error: EastmoneyDataError | None = None
        data = None
        for host in (KLINE_HOST, KLINE_FALLBACK_HOST):
            try:
                data = self._get_json(host, "/api/qt/stock/kline/get", params)
                break
            except EastmoneyDataError as exc:
                last_error = exc
        if data is None:
            raise EastmoneyDataError(
                f"Eastmoney K-line request failed for {symbol}"
            ) from last_error
        rows = []
        for line in ((data.get("data") or {}).get("klines") or []):
            parts = str(line).split(",")
            if len(parts) < 7:
                continue
            trade_date = date.fromisoformat(parts[0])
            if start_date and trade_date < start_date:
                continue
            if end_date and trade_date > end_date:
                continue
            try:
                rows.append(
                    DailyBarRecord(
                        symbol=normalize_symbol(code),
                        trade_date=trade_date,
                        adjust_type="qfq",
                        open=Decimal(parts[1]),
                        close=Decimal(parts[2]),
                        high=Decimal(parts[3]),
                        low=Decimal(parts[4]),
                        volume=Decimal(parts[5]),
                        amount=Decimal(parts[6]),
                        raw_payload={"line": line},
                    )
                )
            except (InvalidOperation, ValueError) as exc:
                raise EastmoneyDataError(f"invalid kline row for {symbol}: {line}") from exc
        return rows

    def _get_json(self, host: str, path: str, params: dict[str, object]) -> dict:
        url = f"{host}{path}?{urlencode(params)}"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                request = Request(url, headers=_HEADERS, method="GET")
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    payload = response.read().decode("utf-8")
                result = loads(payload)
                if not isinstance(result, dict):
                    raise EastmoneyDataError(f"unexpected response from {path}")
                return result
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                JSONDecodeError,
                EastmoneyDataError,
            ) as exc:
                last_error = exc
                if attempt < self.retries:
                    sleep(min(2**attempt, 8))
        raise EastmoneyDataError(f"Eastmoney request failed: {path}") from last_error


def market_prefix(code: str) -> str:
    return "1" if code.startswith(("60", "68", "11", "13", "5")) else "0"


def normalize_symbol(code: str) -> str:
    suffix = ".SH" if market_prefix(code) == "1" else ".SZ"
    if code.startswith(("8", "4")):
        suffix = ".BJ"
    return f"{code}{suffix}"
