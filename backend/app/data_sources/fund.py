from json import JSONDecodeError, loads
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config.settings import settings
from app.data_sources.hithink import HithinkFinanceError

_FUND_TYPES = {"otc", "exchange", "reits"}
_NAV_RANGES = {"week", "month", "tmonth", "hyear", "year", "twoyear", "tyear", "fyear"}
_NAV_TYPES = {"unit", "adj", "unit,adj"}
_MERGE_SCOPES = {"all", "merged", "separate"}


class FundDataSource:
    """REST client for the Fuyao fund capabilities (/api/fund/*).

    Profile/holdings/performance/holders are located by ``(fund_type, thscode)``;
    the market endpoints only accept ``thscode`` and currently cover ETFs only.
    """

    name = "hithink_finance_fund"
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

    def fetch_profile(self, *, fund_type: str, symbol: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/fund/profile/detail",
            {"fund_type": _validate_fund_type(fund_type), "thscode": _norm(symbol)},
        ).get("item", [])

    def fetch_holdings(self, *, fund_type: str, symbol: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/fund/portfolio/holdings",
            {"fund_type": _validate_fund_type(fund_type), "thscode": _norm(symbol)},
        ).get("item", [])

    def fetch_nav(
        self,
        *,
        fund_type: str,
        symbol: str,
        range_: str | None = None,
        nav_type: str = "unit,adj",
    ) -> list[dict[str, Any]]:
        if range_ is not None and range_ not in _NAV_RANGES:
            raise ValueError(f"range must be one of: {sorted(_NAV_RANGES)}")
        if nav_type not in _NAV_TYPES:
            raise ValueError("nav_type must be one of: unit, adj, unit,adj")
        return self._get(
            "/api/fund/performance/nav",
            {
                "fund_type": _validate_fund_type(fund_type),
                "thscode": _norm(symbol),
                "range": range_,
                "nav_type": nav_type,
            },
        ).get("item", [])

    def fetch_returns(self, *, fund_type: str, symbol: str) -> list[dict[str, Any]]:
        return self._get(
            "/api/fund/performance/returns",
            {"fund_type": _validate_fund_type(fund_type), "thscode": _norm(symbol)},
        ).get("item", [])

    def fetch_holders(
        self, *, fund_type: str, symbol: str, merge_scope: str = "all"
    ) -> list[dict[str, Any]]:
        if merge_scope not in _MERGE_SCOPES:
            raise ValueError("merge_scope must be one of: all, merged, separate")
        return self._get(
            "/api/fund/holders/detail",
            {
                "fund_type": _validate_fund_type(fund_type),
                "thscode": _norm(symbol),
                "merge_scope": merge_scope,
            },
        ).get("item", [])

    def fetch_market_snapshot(self, *, symbol: str) -> list[dict[str, Any]]:
        # Market endpoints identify the leaf type from thscode; no fund_type.
        return self._get(
            "/api/fund/market/snapshot", {"thscode": _norm(symbol)}
        ).get("item", [])

    def fetch_market_daily_bars(
        self, *, symbol: str, start: int, end: int
    ) -> list[dict[str, Any]]:
        if end < start:
            raise ValueError("end must be on or after start")
        return self._get(
            "/api/fund/market/historical",
            {"thscode": _norm(symbol), "interval": "1d", "start": start, "end": end},
        ).get("item", [])

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
            raise HithinkFinanceError(f"HiThink fund HTTP error: {exc.code}") from exc
        except URLError as exc:
            raise HithinkFinanceError(f"HiThink fund network error: {exc.reason}") from exc
        except JSONDecodeError as exc:
            raise HithinkFinanceError("HiThink fund response is not valid JSON") from exc

        code = payload.get("code")
        if code != 0:
            raise HithinkFinanceError(
                payload.get("message") or "HiThink fund API returned an error",
                code=code,
                request_id=payload.get("request_id"),
            )
        return payload.get("data") or {}


def _validate_fund_type(fund_type: str) -> str:
    if fund_type not in _FUND_TYPES:
        raise ValueError("fund_type must be one of: otc, exchange, reits")
    return fund_type


def _norm(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise ValueError("thscode is required")
    return symbol.strip().upper()
