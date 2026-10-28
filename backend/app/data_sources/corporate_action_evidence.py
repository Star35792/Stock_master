"""Supplier adapters for dated corporate-action evidence.

These adapters only fetch source rows. Publication into market is a separate
quality and reconciliation decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.request import Request, urlopen

from app.config.settings import settings


@dataclass(frozen=True)
class CorporateActionResponse:
    source_name: str
    endpoint: str
    source_version: str
    rows: list[dict]


class AkshareCorporateActionSource:
    name = "akshare_cninfo"
    source_type = "akshare"
    endpoint = "stock_dividend_cninfo"

    def fetch(self, symbol: str) -> CorporateActionResponse:
        import akshare as ak  # Optional data dependency; only needed when enabled.

        frame = ak.stock_dividend_cninfo(symbol=symbol.split(".", 1)[0])
        required = {"股权登记日", "除权日", "派息日", "实施方案公告日期"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"AKShare dividend columns missing: {sorted(missing)}")
        # Pandas serializes NaT/NaN to null and dates to ISO strings.
        rows = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
        return CorporateActionResponse(self.name, self.endpoint, ak.__version__, rows)


class TushareCorporateActionSource:
    name = "tushare"
    source_type = "api"
    endpoint = "dividend"
    fields = (
        "ts_code,end_date,ann_date,div_proc,stk_bo_rate,stk_co_rate,"
        "cash_div_tax,record_date,ex_date,pay_date,imp_ann_date"
    )

    def fetch(self, symbol: str) -> CorporateActionResponse:
        if not settings.tushare_token:
            raise ValueError("TUSHARE_TOKEN is not configured")
        body = json.dumps(
            {
                "api_name": self.endpoint,
                "token": settings.tushare_token,
                "params": {"ts_code": symbol},
                "fields": self.fields,
            }
        ).encode("utf-8")
        request = Request(
            "https://api.tushare.pro",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=settings.akshare_timeout_seconds) as response:
            payload = json.load(response, parse_float=str)
        if payload.get("code") != 0:
            # Never include the request body (and therefore the token) in errors.
            raise RuntimeError(f"Tushare {self.endpoint} rejected request: {payload.get('msg')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        required = {"ts_code", "record_date", "ex_date", "pay_date", "imp_ann_date"}
        if not required.issubset(fields):
            raise ValueError("Tushare dividend response is missing required fields")
        items = data.get("items") or []
        if any(len(item) != len(fields) for item in items):
            raise ValueError("Tushare dividend response has malformed rows")
        rows = [dict(zip(fields, item, strict=True)) for item in items]
        return CorporateActionResponse(self.name, self.endpoint, "pro", rows)


class EastmoneyCorporateActionSource:
    """Eastmoney dividend history; this endpoint has no payment date."""

    name = "eastmoney_via_akshare"
    source_type = "akshare"
    endpoint = "stock_fhps_detail_em"

    def fetch(self, symbol: str) -> CorporateActionResponse:
        import akshare as ak

        frame = ak.stock_fhps_detail_em(symbol=symbol.split(".", 1)[0])
        required = {"股权登记日", "除权除息日", "最新公告日期"}
        if not required.issubset(frame.columns):
            missing = sorted(required - set(frame.columns))
            raise ValueError(f"Eastmoney dividend columns missing: {missing}")
        rows = json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
        return CorporateActionResponse(self.name, self.endpoint, ak.__version__, rows)
