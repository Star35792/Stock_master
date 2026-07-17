from __future__ import annotations

from datetime import date

from app.data_sources.base import DailyBarRecord
from app.data_sources.eastmoney import EastmoneyDataError, EastmoneyDataSource
from app.data_sources.xueqiu import XueqiuDataError, XueqiuDataSource


class FallbackDailyBarDataSource:
    name = "eastmoney_xueqiu_fallback"
    source_type = "api"

    def __init__(
        self,
        *,
        eastmoney: EastmoneyDataSource | None = None,
        xueqiu: XueqiuDataSource | None = None,
    ) -> None:
        self.eastmoney = eastmoney or EastmoneyDataSource()
        self.xueqiu = xueqiu or XueqiuDataSource()
        self.last_source_name: str | None = None

    def fetch_daily_bars(
        self,
        *,
        symbol: str,
        start_date: date,
        end_date: date,
        adjust_type: str = "qfq",
    ) -> list[DailyBarRecord]:
        errors: list[str] = []
        try:
            rows = self.eastmoney.fetch_daily_bars(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
            )
            if rows:
                self.last_source_name = getattr(self.eastmoney, "name", "eastmoney")
                return rows
            errors.append("eastmoney returned no rows")
        except (EastmoneyDataError, RuntimeError, ValueError) as exc:
            errors.append(f"eastmoney: {exc}")
        try:
            rows = self.xueqiu.fetch_daily_bars(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
            )
            if rows:
                self.last_source_name = getattr(self.xueqiu, "name", "xueqiu")
                return rows
            errors.append("xueqiu returned no rows")
        except (XueqiuDataError, ValueError) as exc:
            errors.append(f"xueqiu: {exc}")
        raise RuntimeError(f"all fallback K-line sources failed for {symbol}: {'; '.join(errors)}")
