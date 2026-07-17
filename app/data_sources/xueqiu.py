from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from json import JSONDecodeError, loads
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.config.settings import settings
from app.data_sources.base import DailyBarRecord

_CN_TZ = timezone(timedelta(hours=8))
_URL = "https://stock.xueqiu.com/v5/stock/chart/kline.json"


class XueqiuDataError(RuntimeError):
    pass


class XueqiuDataSource:
    name = "xueqiu"
    source_type = "api"

    def __init__(self, *, cookie: str | None = None, timeout_seconds: int | None = None) -> None:
        self.cookie = cookie if cookie is not None else settings.xueqiu_cookie
        self.timeout_seconds = timeout_seconds or settings.xueqiu_timeout_seconds

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust_type: str = "qfq",
    ) -> list[DailyBarRecord]:
        if adjust_type != "qfq":
            raise ValueError("Xueqiu K-lines currently support qfq only")
        code, exchange = _split_symbol(symbol)
        span = max((end_date - start_date).days + 1, 1)
        params = {
            "symbol": f"{exchange}{code}",
            "begin": str(_timestamp_ms(end_date)),
            "period": "day",
            "type": "before",
            "count": str(-min(span * 2 + 20, 1023)),
            "indicator": "kline,pe,pb,ps,pcf,market_capital,agt,ggt,balance",
        }
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://xueqiu.com/S/{exchange}{code}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if self.cookie:
            headers["Cookie"] = self.cookie
        try:
            request = Request(f"{_URL}?{urlencode(params)}", headers=headers)
            with urlopen(request, timeout=self.timeout_seconds) as response:
                payload = loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, JSONDecodeError) as exc:
            raise XueqiuDataError(f"Xueqiu K-line request failed for {symbol}") from exc
        if payload.get("error_code") not in (None, 0, "0"):
            raise XueqiuDataError(
                f"Xueqiu K-line rejected {symbol}: "
                f"{payload.get('error_description') or payload.get('error_code')}"
            )
        data = payload.get("data") or {}
        columns = data.get("column") or []
        records = []
        for values in data.get("item") or []:
            row = dict(zip(columns, values, strict=False))
            try:
                trade_date = datetime.fromtimestamp(
                    float(row["timestamp"]) / 1000, tz=_CN_TZ
                ).date()
                if not start_date <= trade_date <= end_date:
                    continue
                records.append(
                    DailyBarRecord(
                        symbol=f"{code}.{exchange}",
                        trade_date=trade_date,
                        adjust_type="qfq",
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=Decimal(str(row.get("volume", 0))),
                        amount=Decimal(str(row.get("amount", 0))),
                        raw_payload=row,
                    )
                )
            except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
                raise XueqiuDataError(f"invalid Xueqiu K-line row for {symbol}") from exc
        if not records and not data:
            raise XueqiuDataError(f"Xueqiu returned no K-line data for {symbol}")
        return records


def _split_symbol(symbol: str) -> tuple[str, str]:
    value = symbol.strip().upper()
    code, _, suffix = value.partition(".")
    if not code.isdigit() or len(code) != 6:
        raise ValueError("symbol must contain a six-digit code")
    if suffix not in {"SH", "SZ"}:
        suffix = "SH" if code.startswith(("60", "68", "5")) else "SZ"
    return code, suffix


def _timestamp_ms(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, 15, tzinfo=_CN_TZ).timestamp() * 1000)
