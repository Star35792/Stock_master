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
VALUATION_HISTORY_HOST = "https://datacenter-web.eastmoney.com"
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

    def fetch_valuation_snapshots(
        self,
        *,
        market: str = "all",
        limit: int = 10000,
    ) -> list[dict[str, object]]:
        """Bulk-fetch current valuation snapshots (market cap/PE/PB), cookie-free.

        Paginates the clist endpoint across the whole market. ``trade_date`` is
        today; run daily to accumulate historical valuation snapshots.
        """
        market_filters = {
            "all": "m:0+t:6,m:0+t:13,m:0+t:80,m:1+t:2,m:1+t:23,m:1+t:8",
            "sh": "m:0+t:6,m:0+t:13,m:0+t:80",
            "sz": "m:1+t:2,m:1+t:23,m:1+t:8",
        }
        if market not in market_filters:
            raise ValueError(f"unsupported market: {market}")
        if limit < 1:
            raise ValueError("limit must be positive")
        page_size = 100
        rows: list[dict[str, object]] = []
        pn = 1
        while len(rows) < limit:
            data = self._get_json(
                QUOTE_HOST,
                "/api/qt/clist/get",
                {
                    "pn": pn,
                    "pz": page_size,
                    "po": 1,
                    "np": 1,
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": market_filters[market],
                    "fields": "f12,f2,f9,f23,f20,f21",
                },
            )
            diff = (data.get("data") or {}).get("diff") or []
            if not diff:
                break
            for item in diff:
                code = item.get("f12")
                if not code:
                    continue
                rows.append(
                    {
                        "symbol": normalize_symbol(str(code)),
                        "trade_date": date.today(),
                        "close": _to_decimal(item.get("f2")),
                        "total_market_cap": _to_decimal(item.get("f20")),
                        "float_market_cap": _to_decimal(item.get("f21")),
                        "pe_ttm": _to_decimal(item.get("f9")),
                        "pb": _to_decimal(item.get("f23")),
                        "ps_ttm": None,
                        "currency": "CNY",
                        "source_name": self.name,
                        "raw_payload": item,
                    }
                )
                if len(rows) >= limit:
                    break
            if len(diff) < page_size:
                break
            pn += 1
        return rows

    def iter_valuation_snapshot_pages(self, *, limit: int = 10000):
        """Yield complete current clist pages with the vendor's reported total.

        These quotes contain no vendor as-of timestamp. Callers must record
        collection time and validate against same-day trusted closes.
        """
        if limit < 1:
            raise ValueError("limit must be positive")
        page_size = 100
        page = 1
        expected_total = None
        received = 0
        while expected_total is None or received < expected_total:
            params = {
                "pn": page,
                "pz": page_size,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f12",
                "fs": (
                    "m:0+t:6,m:0+t:13,m:0+t:80,"
                    "m:1+t:2,m:1+t:23,m:1+t:8,m:0+t:81+s:2048"
                ),
                "fields": "f12,f13,f2,f9,f23,f20,f21",
            }
            try:
                response, raw_text = self._get_json_with_raw(
                    QUOTE_HOST, "/api/qt/clist/get", params
                )
            except EastmoneyDataError:
                response, raw_text = self._get_json_with_raw(
                    "https://push2.eastmoney.com", "/api/qt/clist/get", params
                )
            data = response.get("data") or {}
            total = data.get("total")
            items = data.get("diff") or []
            if not isinstance(total, int) or total < 1 or total > limit:
                raise EastmoneyDataError("invalid or oversized valuation page total")
            if expected_total is not None and total != expected_total:
                raise EastmoneyDataError("valuation total changed during pagination")
            expected_total = total
            if not isinstance(items, list) or not items:
                raise EastmoneyDataError("incomplete valuation pagination")
            if len(items) > min(page_size, total - received):
                raise EastmoneyDataError("valuation page exceeds reported total")
            yield page, total, items, raw_text
            received += len(items)
            if received < total and len(items) != page_size:
                raise EastmoneyDataError("short valuation page before reported total")
            page += 1

    def fetch_historical_valuation_page(
        self, *, trade_date: date, page: int, page_size: int = 500
    ) -> tuple[dict, str]:
        """Fetch one supplier dated valuation page and its unmodified JSON text.

        The requested trade date is a supplier label, not proof of when a
        historical value first became available.
        """
        if page < 1 or not 1 <= page_size <= 500:
            raise ValueError("invalid historical valuation page")
        return self._get_json_with_raw(
            VALUATION_HISTORY_HOST,
            "/api/data/v1/get",
            {
                "reportName": "RPT_VALUEANALYSIS_DET",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(TRADE_DATE='{trade_date.isoformat()}')",
                "pageNumber": page,
                "pageSize": page_size,
                "sortColumns": "SECURITY_CODE",
                "sortTypes": 1,
            },
        )

    def iter_same_day_dated_valuation_pages(self, *, trade_date: date):
        """Use the supplier's dated valuation table as a same-evening fallback.

        A caller must independently check that capture is on trade_date after
        close and that every price matches a trusted unadjusted daily bar.
        """
        received = 0
        total = None
        page = 1
        while total is None or received < total:
            response, raw_text = self.fetch_historical_valuation_page(
                trade_date=trade_date, page=page, page_size=500
            )
            result = response.get("result")
            if response.get("success") is not True or not isinstance(result, dict):
                raise EastmoneyDataError("supplier rejected same-day dated valuation page")
            count = result.get("count")
            items = result.get("data")
            if (
                not isinstance(count, int) or isinstance(count, bool)
                or not 1 <= count <= 10000 or not isinstance(items, list)
                or not items or (total is not None and count != total)
                or len(items) != min(500, count - received)
            ):
                raise EastmoneyDataError("same-day dated valuation pagination is incomplete")
            total = count
            normalized = []
            for item in items:
                if not isinstance(item, dict):
                    raise EastmoneyDataError("invalid same-day dated valuation row")
                try:
                    item_date = date.fromisoformat(str(item.get("TRADE_DATE"))[:10])
                except ValueError as exc:
                    raise EastmoneyDataError("invalid supplier valuation date") from exc
                if item_date != trade_date:
                    raise EastmoneyDataError("supplier valuation has wrong trade date")
                code = item.get("SECURITY_CODE")
                symbol = item.get("SECUCODE")
                if not isinstance(code, str) or symbol != normalize_symbol(code):
                    raise EastmoneyDataError("invalid supplier security code")
                normalized.append({
                    "f12": code,
                    "f13": 1 if symbol.endswith(".SH") else 0,
                    "f2": item.get("CLOSE_PRICE"),
                    "f9": item.get("PE_TTM"),
                    "f23": item.get("PB_MRQ"),
                    "f20": item.get("TOTAL_MARKET_CAP"),
                    "f21": item.get("NOTLIMITED_MARKETCAP_A"),
                    "_supplier_endpoint": "RPT_VALUEANALYSIS_DET",
                    "_supplier_payload": item,
                })
            yield page, total, normalized, raw_text
            received += len(items)
            page += 1

    def fetch_reported_roe_page(
        self, *, report_date: date, page: int, page_size: int = 500
    ) -> tuple[dict, str]:
        """Fetch a page of issuer reported, weighted-average ROE evidence.

        REPORTDATE is the fiscal period, not a first-available timestamp. The
        caller must retain NOTICE_DATE, EITIME and the actual capture time.
        """
        if page < 1 or not 1 <= page_size <= 500:
            raise ValueError("invalid reported ROE page")
        return self._get_json_with_raw(
            VALUATION_HISTORY_HOST,
            "/api/data/v1/get",
            {
                "reportName": "RPT_LICO_FN_CPD",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(REPORTDATE='{report_date.isoformat()}')",
                "pageNumber": page,
                "pageSize": page_size,
                "sortColumns": "SECURITY_CODE",
                "sortTypes": 1,
            },
        )

    def _get_json(self, host: str, path: str, params: dict[str, object]) -> dict:
        return self._get_json_with_raw(host, path, params)[0]

    def _get_json_with_raw(
        self, host: str, path: str, params: dict[str, object]
    ) -> tuple[dict, str]:
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
                return result, payload
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


def _to_decimal(value: object) -> Decimal | None:
    if value in (None, "", "-", "—"):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def market_prefix(code: str) -> str:
    return "1" if code.startswith(("60", "68", "11", "13", "5")) else "0"


def normalize_symbol(code: str) -> str:
    suffix = ".SH" if market_prefix(code) == "1" else ".SZ"
    if code.startswith(("8", "4", "92")):
        suffix = ".BJ"
    return f"{code}{suffix}"
