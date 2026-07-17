from datetime import date
from decimal import Decimal

from app.data_sources.base import DailyBarRecord
from app.data_sources.fallback import FallbackDailyBarDataSource
from app.data_sources.xueqiu import XueqiuDataSource


def test_xueqiu_source_parses_rows(monkeypatch) -> None:
    source = XueqiuDataSource(cookie="cookie")

    class Response:
        def read(self):
            return (
                b'{"error_code":0,"data":{"column":["timestamp","open","close",'
                b'"high","low","volume","amount"],"item":[[1784217600000,10,11,12,9,100,1000]]}}'
            )

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("app.data_sources.xueqiu.urlopen", lambda *args, **kwargs: Response())
    rows = source.fetch_daily_bars(
        symbol="600519.SH", start_date=date(2026, 7, 17), end_date=date(2026, 7, 17)
    )

    assert rows[0].symbol == "600519.SH"
    assert rows[0].close == Decimal("11")


def test_fallback_uses_xueqiu_after_eastmoney_failure() -> None:
    class FailedEastmoney:
        def fetch_daily_bars(self, **kwargs):
            raise RuntimeError("disconnected")

    class Xueqiu:
        def fetch_daily_bars(self, **kwargs):
            return [
                DailyBarRecord(
                    symbol="600519.SH",
                    trade_date=date(2026, 7, 17),
                    adjust_type="qfq",
                    open=Decimal("1"),
                    high=Decimal("1"),
                    low=Decimal("1"),
                    close=Decimal("1"),
                )
            ]

    source = FallbackDailyBarDataSource(eastmoney=FailedEastmoney(), xueqiu=Xueqiu())
    rows = source.fetch_daily_bars(
        symbol="600519.SH",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
    )

    assert rows[0].symbol == "600519.SH"
