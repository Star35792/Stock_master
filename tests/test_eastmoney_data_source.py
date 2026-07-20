from datetime import date

from app.data_sources.eastmoney import (
    KLINE_FALLBACK_HOST,
    KLINE_HOST,
    EastmoneyDataError,
    EastmoneyDataSource,
)
from app.services.eastmoney_collection import _money, _normalize_rows


def test_eastmoney_source_parses_qfq_klines(monkeypatch) -> None:
    source = EastmoneyDataSource(retries=0)
    monkeypatch.setattr(
        source,
        "_get_json",
        lambda *args, **kwargs: {
            "data": {
                "klines": [
                    "2026-07-17,10.1,10.2,10.3,9.9,1000,10200,1.0,1.0,0.1,0.2"
                ]
            }
        },
    )

    rows = source.fetch_daily_bars(symbol="600519.SH", count=10)

    assert rows[0].symbol == "600519.SH"
    assert rows[0].adjust_type == "qfq"
    assert rows[0].trade_date == date(2026, 7, 17)
    assert rows[0].amount == 10200


def test_eastmoney_kline_uses_fallback_host(monkeypatch) -> None:
    source = EastmoneyDataSource(retries=0)
    hosts = []

    def fake_get(host, path, params):
        hosts.append(host)
        if host == KLINE_HOST:
            raise EastmoneyDataError("disconnected")
        return {"data": {"klines": []}}

    monkeypatch.setattr(source, "_get_json", fake_get)
    assert source.fetch_daily_bars(symbol="600519", count=10) == []
    assert hosts == [KLINE_HOST, KLINE_FALLBACK_HOST]


def test_eastmoney_flow_rows_parse_units_and_sector_symbols() -> None:
    rows = _normalize_rows(
        "eastmoney_industry_fund_flow",
        [{"code": "BK0428", "name": "电力", "main_net_inflow": "5.53亿"}],
        date(2026, 7, 17),
        "eastmoney",
    )

    assert rows[0]["symbol"] is None
    assert rows[0]["value"] == 553000000
    assert _money("12.5万") == 125000


def test_eastmoney_flow_rows_parse_native_field_names() -> None:
    rows = _normalize_rows(
        "eastmoney_main_fund_rank",
        [{"f12": "600519", "f14": "贵州茅台", "f62": "1.5亿", "f184": "2.5"}],
        date(2026, 7, 17),
        "eastmoney",
    )

    assert rows[0]["item_key"] == "600519"
    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["value"] == 150000000
    assert rows[0]["attributes"]["f184"] == "2.5"
