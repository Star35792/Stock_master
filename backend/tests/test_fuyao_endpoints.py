from datetime import date

import pytest

from app.data_sources import FundDataSource, HithinkFinanceDataSource


class _Capture:
    """Records the (path, params) each client method hands to ``_get``."""

    def __init__(self, payload: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.payload = payload if payload is not None else {"item": []}

    def __call__(self, path: str, params: dict) -> dict:
        self.calls.append((path, params))
        return self.payload


def _client(payload: dict | None = None) -> tuple[HithinkFinanceDataSource, _Capture]:
    client = HithinkFinanceDataSource(api_key="x")
    capture = _Capture(payload)
    client._get = capture  # type: ignore[method-assign]
    return client, capture


def test_search_tickers_builds_query() -> None:
    client, capture = _client()
    client.search_tickers(keyword="  贵州茅台 ", asset_type="a-share", limit=20)
    path, params = capture.calls[0]
    assert path == "/api/meta/tickers/search"
    assert params == {"q": "贵州茅台", "exchange": None, "asset_type": "a-share", "limit": 20}


def test_search_tickers_validates_limit() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        client.search_tickers(keyword="x", limit=51)


def test_fetch_adjustment_factors_formats_dates() -> None:
    client, capture = _client()
    client.fetch_adjustment_factors(
        symbol="600519.sh", from_date=date(2020, 1, 1), to_date=date(2026, 1, 1)
    )
    path, params = capture.calls[0]
    assert path == "/api/a-share/corporate-actions/adjustment-factors"
    assert params == {"thscode": "600519.SH", "from": "2020-01-01", "to": "2026-01-01"}


def test_fetch_index_snapshots_joins_symbols() -> None:
    client, capture = _client()
    client.fetch_index_snapshots(symbols=["000001.sh", " 399001.SZ "])
    path, params = capture.calls[0]
    assert path == "/api/a-share-index/prices/snapshot"
    assert params == {"thscodes": "000001.SH,399001.SZ"}


def test_fetch_index_daily_bars_maps_bars() -> None:
    payload = {
        "item": [
            {
                "date_ms": 1716134400000,
                "open_price": 3108.22,
                "high_price": 3125.74,
                "low_price": 3101.15,
                "close_price": 3120.68,
                "volume": 281000000,
                "turnover": 360000000000,
            }
        ]
    }
    client, capture = _client(payload)
    bars = client.fetch_index_daily_bars(
        symbol="000001.SH", start_date=date(2024, 5, 1), end_date=date(2024, 5, 20)
    )
    path, params = capture.calls[0]
    assert path == "/api/a-share-index/prices/historical"
    assert params["thscode"] == "000001.SH"
    assert params["interval"] == "1d"
    assert bars[0].trade_date == date(2024, 5, 20)
    assert str(bars[0].close) == "3120.68"


def test_hot_stock_rank_trend_rejects_reversed_window() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        client.fetch_hot_stock_rank_trend(
            symbol="300034.SZ", start_date=date(2026, 7, 1), end_date=date(2026, 6, 1)
        )


def test_anomaly_by_stock_caps_symbol_count() -> None:
    client, _ = _client()
    with pytest.raises(ValueError):
        client.fetch_anomaly_by_stock(symbols=[f"{i:06d}.SZ" for i in range(51)])


def _fund(payload: dict | None = None) -> tuple[FundDataSource, _Capture]:
    client = FundDataSource(api_key="x")
    capture = _Capture(payload)
    client._get = capture  # type: ignore[method-assign]
    return client, capture


def test_fund_profile_requires_valid_fund_type() -> None:
    client, _ = _fund()
    with pytest.raises(ValueError):
        client.fetch_profile(fund_type="bogus", symbol="025480.OF")


def test_fund_nav_builds_query() -> None:
    client, capture = _fund()
    client.fetch_nav(fund_type="exchange", symbol="510300.sh", range_="year", nav_type="unit")
    path, params = capture.calls[0]
    assert path == "/api/fund/performance/nav"
    assert params == {
        "fund_type": "exchange",
        "thscode": "510300.SH",
        "range": "year",
        "nav_type": "unit",
    }


def test_fund_market_snapshot_omits_fund_type() -> None:
    client, capture = _fund()
    client.fetch_market_snapshot(symbol="510300.SH")
    path, params = capture.calls[0]
    assert path == "/api/fund/market/snapshot"
    assert params == {"thscode": "510300.SH"}
