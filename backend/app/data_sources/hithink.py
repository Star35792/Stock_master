from datetime import date, datetime, time
from decimal import Decimal
from json import JSONDecodeError, loads
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app.config.settings import settings
from app.data_sources.base import AdjustType, DailyBarRecord, SecurityRecord, TradingDayRecord

_ADJUST_TO_HITHINK = {
    "none": "none",
    "qfq": "forward",
    "hfq": "backward",
}
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class HithinkFinanceError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, request_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class HithinkFinanceDataSource:
    name = "hithink_finance"
    source_type = "api"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key or settings.hithink_finance_api_key
        self.base_url = (base_url or settings.hithink_finance_base_url).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.hithink_finance_timeout_seconds

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust_type: AdjustType = "none",
    ) -> list[DailyBarRecord]:
        if adjust_type not in _ADJUST_TO_HITHINK:
            raise ValueError(f"unsupported adjust_type: {adjust_type}")

        params = {
            "thscode": symbol.upper(),
            "interval": "1d",
            "start": _date_to_ms(start_date),
            "end": _date_to_ms(end_date),
            "adjust": _ADJUST_TO_HITHINK[adjust_type],
        }
        offset = 0
        records: list[DailyBarRecord] = []
        while True:
            data = self._get(
                "/api/a-share/prices/historical",
                {**params, "offset": offset},
            )
            page = data.get("item", [])
            records.extend(
                self._to_daily_bar(symbol.upper(), adjust_type, item) for item in page
            )
            if not page:
                return records
            next_offset = data.get("next_offset")
            if next_offset is None:
                total = data.get("total")
                if total is not None and offset + len(page) >= int(total):
                    return records
                next_offset = offset + len(page)
            next_offset = int(next_offset)
            if next_offset <= offset:
                raise HithinkFinanceError("HiThink historical pagination did not advance")
            offset = next_offset

    def fetch_price_snapshots(
        self,
        *,
        symbols: list[str] | None = None,
        fetch_all_market: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if symbols and fetch_all_market:
            raise ValueError("symbols and fetch_all_market cannot be provided together")
        if symbols:
            return self._get(
                "/api/a-share/prices/snapshot",
                {"thscodes": ",".join(symbol.strip().upper() for symbol in symbols)},
            ).get("item", [])
        if not fetch_all_market:
            return self._get(
                "/api/a-share/prices/snapshot",
                {"limit": limit, "offset": offset},
            ).get("item", [])
        if limit < 1 or limit > 10000:
            raise ValueError("limit must be in [1, 10000]")

        items: list[dict[str, Any]] = []
        current_offset = offset
        while True:
            page = self._get(
                "/api/a-share/prices/snapshot",
                {"limit": limit, "offset": current_offset},
            ).get("item", [])
            items.extend(page)
            if len(page) < limit:
                return items
            current_offset += limit

    def fetch_securities(
        self,
        *,
        asset_type: str = "a-share",
        exchanges: tuple[str, ...] = ("SH", "SZ", "BJ"),
        limit: int = 10000,
    ) -> list[SecurityRecord]:
        offset = 0
        records: list[SecurityRecord] = []
        while True:
            data = self._get(
                "/api/meta/tickers/list",
                {
                    "asset_type": asset_type,
                    "exchange": ",".join(exchanges),
                    "limit": limit,
                    "offset": offset,
                },
            )
            page = data.get("item", [])
            records.extend(self._to_security(item) for item in page)
            if len(page) < limit:
                return records
            offset += limit

    def fetch_trading_days(
        self, *, exchanges: tuple[str, ...] = ("SH", "SZ", "BJ")
    ) -> list[TradingDayRecord]:
        data = self._get("/api/a-share/calendar/trading-days", {})
        days = [
            datetime.fromtimestamp(item["date_ms"] / 1000, tz=_SHANGHAI).date()
            for item in data.get("item", [])
            if item.get("date_ms") is not None
        ]
        return [
            TradingDayRecord(exchange=exchange, trade_date=trade_date)
            for exchange in exchanges
            for trade_date in days
        ]

    def fetch_financial_statements(
        self,
        *,
        symbol: str,
        statement_type: str,
        period: str = "annual",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        paths = {
            "income": "/api/a-share/financials/income-statements",
            "balance": "/api/a-share/financials/balance-sheets",
            "cash_flow": "/api/a-share/financials/cash-flow-statements",
        }
        if statement_type not in paths:
            raise ValueError("statement_type must be one of: income, balance, cash_flow")
        if period not in {"annual", "quarterly"}:
            raise ValueError("period must be one of: annual, quarterly")
        if limit < 1:
            raise ValueError("limit must be positive")
        data = self._get(
            paths[statement_type],
            {
                "thscode": symbol.strip().upper(),
                "period": period,
                "limit": limit,
            },
        )
        return data.get("item", [])

    def fetch_financial_indicators(self, *, symbol: str, report: str) -> dict[str, Any]:
        year, separator, quarter = report.partition("-")
        if (
            separator != "-"
            or len(year) != 4
            or not year.isdigit()
            or len(quarter) != 1
            or quarter not in "1234"
        ):
            raise ValueError("report must match YYYY-[1-4]")
        return self._get(
            "/api/a-share/financials/indicators",
            {"thscode": symbol.strip().upper(), "report": report},
        )

    def fetch_ths_indices(self, *, tag: str = "industry") -> list[dict[str, Any]]:
        if tag not in {"cn_concept", "region", "tszs", "industry"}:
            raise ValueError("unsupported index tag")
        return self._get(
            "/api/a-share-index/catalog/ths-index-list", {"tag": tag}
        ).get("item", [])

    def fetch_index_constituents(self, *, symbol: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/a-share-index/constituents/ths-stock-list",
            {"thscode": symbol.strip().upper()},
        ).get("item", [])

    def fetch_limit_up_pool(self, *, date_ms: int | None = None) -> dict[str, Any]:
        page = 1
        items: list[dict[str, Any]] = []
        result: dict[str, Any] = {}
        while True:
            result = self._get(
                "/api/a-share/special-data/limit-up-pool",
                {
                    "date_ms": date_ms,
                    "page": page,
                    "size": 200,
                    "sort_field": "last_price",
                    "sort_dir": "desc",
                },
            )
            current = result.get("item", [])
            items.extend(current)
            pages = int((result.get("pagination") or {}).get("pages") or 1)
            if page >= pages or not current:
                return {**result, "item": items}
            page += 1

    def fetch_limit_up_ladder(self) -> dict[str, Any]:
        return self._get("/api/a-share/special-data/limit-up-ladder", {})

    def fetch_anomaly_list(self, *, tag_codes: list[str] | None = None) -> dict[str, Any]:
        return self._get(
            "/api/a-share/special-data/anomaly-analysis-list",
            {"tag_codes": ",".join(tag_codes) if tag_codes else None},
        )

    def fetch_hot_stock_list(self, *, period: str = "day") -> dict[str, Any]:
        if period not in {"day", "hour"}:
            raise ValueError("period must be one of: day, hour")
        return self._get(
            "/api/a-share/special-data/hot-stock-list", {"period": period}
        )

    def fetch_skyrocket_list(self, *, period: str = "day") -> dict[str, Any]:
        if period not in {"day", "hour"}:
            raise ValueError("period must be one of: day, hour")
        return self._get(
            "/api/a-share/special-data/skyrocket-list", {"period": period}
        )

    def fetch_dragon_tiger_list(
        self, *, board_type: str = "all", trade_date: date | None = None
    ) -> dict[str, Any]:
        if board_type not in {"all", "org", "hot_money"}:
            raise ValueError("board_type must be one of: all, org, hot_money")
        return self._get(
            "/api/a-share/special-data/dragon-tiger-list",
            {
                "board_type": board_type,
                "date": trade_date.isoformat() if trade_date else None,
            },
        )

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise HithinkFinanceError("HITHINK_FINANCE_API_KEY is required")

        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = f"{self.base_url}{path}?{query}"
        request = Request(url, headers={"X-api-key": self.api_key}, method="GET")

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise HithinkFinanceError(f"HiThink HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise HithinkFinanceError(f"HiThink network error: {exc.reason}") from exc
        except JSONDecodeError as exc:
            raise HithinkFinanceError("HiThink response is not valid JSON") from exc

        code = payload.get("code")
        if code != 0:
            raise HithinkFinanceError(
                payload.get("message") or "HiThink API returned an error",
                code=code,
                request_id=payload.get("request_id"),
            )
        return payload.get("data") or {}

    @staticmethod
    def _to_daily_bar(
        symbol: str,
        adjust_type: AdjustType,
        item: dict[str, Any],
    ) -> DailyBarRecord:
        return DailyBarRecord(
            symbol=symbol,
            trade_date=datetime.fromtimestamp(item["date_ms"] / 1000, tz=_SHANGHAI).date(),
            adjust_type=adjust_type,
            open=Decimal(str(item["open_price"])),
            high=Decimal(str(item["high_price"])),
            low=Decimal(str(item["low_price"])),
            close=Decimal(str(item["close_price"])),
            volume=_optional_decimal(item.get("volume")),
            amount=_optional_decimal(item.get("turnover")),
            raw_payload=item,
        )

    @staticmethod
    def _to_security(item: dict[str, Any]) -> SecurityRecord:
        symbol = str(item["thscode"]).upper()
        asset_type = str(item.get("asset_type") or "")
        return SecurityRecord(
            symbol=symbol,
            raw_symbol=item.get("ticker"),
            name=item.get("name") or symbol,
            security_type=_to_security_type(asset_type),
            exchange=item.get("exchange") or _exchange_from_symbol(symbol),
            currency=item.get("currency") or "CNY",
        )


def _date_to_ms(d: date) -> int:
    """Convert a date to Asia/Shanghai midnight millisecond timestamp."""
    return int(datetime.combine(d, time.min, tzinfo=_SHANGHAI).timestamp() * 1000)


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _to_security_type(asset_type: str) -> str:
    if asset_type == "a-share":
        return "stock"
    if asset_type in {"etf", "a-share-etf"}:
        return "etf"
    if asset_type == "a-share-index":
        return "index"
    return "unknown"


def _exchange_from_symbol(symbol: str) -> str:
    if "." not in symbol:
        return "UNKNOWN"
    exchange = symbol.rsplit(".", 1)[1]
    return exchange if exchange in {"SH", "SZ", "BJ", "HK"} else "UNKNOWN"
