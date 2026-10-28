"""Fetch exchange lifecycle lists as untrusted source evidence."""

from __future__ import annotations

import json
from urllib.request import Request, urlopen

from app.config.settings import settings


class TushareDelistSource:
    name = "tushare"
    source_type = "api"
    endpoint = "stock_basic"

    def fetch(self) -> list[dict]:
        if not settings.tushare_token:
            raise ValueError("TUSHARE_TOKEN is not configured")
        body = json.dumps(
            {
                "api_name": self.endpoint,
                "token": settings.tushare_token,
                "params": {"list_status": "D"},
                "fields": "ts_code,name,list_date,delist_date",
            }
        ).encode()
        request = Request(
            "https://api.tushare.pro",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=settings.akshare_timeout_seconds) as response:
            payload = json.load(response)
        if payload.get("code") != 0:
            raise RuntimeError(f"Tushare stock_basic request rejected: {payload.get('code')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        if not {"ts_code", "delist_date"}.issubset(fields):
            raise ValueError("Tushare delist response is missing required fields")
        items = data.get("items") or []
        if any(len(item) != len(fields) for item in items):
            raise ValueError("Tushare delist response has malformed rows")
        return [dict(zip(fields, item, strict=True)) for item in items]


class AkshareDelistSource:
    name = "akshare_exchange"
    source_type = "akshare"

    def fetch(self, exchange: str) -> list[dict]:
        import akshare as ak

        if exchange == "SZ":
            frame = ak.stock_info_sz_delist(symbol="终止上市公司")
            required = {"证券代码", "终止上市日期"}
        elif exchange == "SH":
            frame = ak.stock_info_sh_delist(symbol="全部")
            # This endpoint exposes suspension dates. It is useful as an
            # independent lifecycle list, never as a delisting date.
            required = {"公司代码", "暂停上市日期"}
        else:
            raise ValueError("exchange must be SH or SZ")
        if not required.issubset(frame.columns):
            raise ValueError(f"AKShare {exchange} lifecycle columns missing")
        return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
