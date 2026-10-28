"""Fetch untrusted historical turnover observations from external suppliers."""

from __future__ import annotations

import json
from datetime import date
from urllib.request import Request, urlopen

from app.config.settings import settings


class TushareTurnoverSource:
    name = "tushare"
    source_type = "api"
    endpoint = "daily_basic"
    fields = "ts_code,trade_date,close,turnover_rate,turnover_rate_f,float_share"

    def fetch(self, trade_date: date) -> list[dict]:
        if not settings.tushare_token:
            raise ValueError("TUSHARE_TOKEN is not configured")
        body = json.dumps(
            {
                "api_name": self.endpoint,
                "token": settings.tushare_token,
                "params": {"trade_date": trade_date.strftime("%Y%m%d")},
                "fields": self.fields,
            }
        ).encode()
        request = Request(
            "https://api.tushare.pro",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=settings.akshare_timeout_seconds) as response:
            payload = json.load(response, parse_float=str)
        if payload.get("code") != 0:
            raise RuntimeError(f"Tushare {self.endpoint} request rejected: {payload.get('code')}")
        data = payload.get("data") or {}
        fields = data.get("fields") or []
        required = {"ts_code", "trade_date", "close", "turnover_rate"}
        if not required.issubset(fields):
            raise ValueError("Tushare turnover response is missing required fields")
        items = data.get("items") or []
        if any(len(item) != len(fields) for item in items):
            raise ValueError("Tushare turnover response contains malformed rows")
        return [dict(zip(fields, item, strict=True)) for item in items]


class AkshareTurnoverSource:
    name = "akshare_eastmoney"
    source_type = "akshare"
    endpoint = "stock_zh_a_hist"

    def fetch(self, symbol: str, start_date: date, end_date: date) -> list[dict]:
        import akshare as ak

        frame = ak.stock_zh_a_hist(
            symbol=symbol.split(".", 1)[0],
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust="",
        )
        required = {"日期", "股票代码", "收盘", "换手率"}
        if not required.issubset(frame.columns):
            raise ValueError("AKShare turnover response is missing required fields")
        return json.loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
