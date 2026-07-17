from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.data_sources.hithink import HithinkFinanceDataSource
from app.services.daily_bar_market_builder import _validate_candidate


def _raw_bar(**overrides):
    values = {
        "id": 1,
        "symbol": "600519.SH",
        "trade_date": date(2026, 7, 17),
        "adjust_type": "none",
        "source_name": "hithink_finance",
        "open": Decimal("1200.00"),
        "high": Decimal("1250.00"),
        "low": Decimal("1190.00"),
        "close": Decimal("1242.47"),
        "volume": Decimal("1000"),
        "amount": Decimal("1242470"),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_hithink_security_mapping() -> None:
    record = HithinkFinanceDataSource._to_security(
        {
            "thscode": "600519.SH",
            "ticker": "600519",
            "name": "贵州茅台",
            "exchange": "SH",
            "asset_type": "a-share",
            "currency": "CNY",
        }
    )

    assert record.symbol == "600519.SH"
    assert record.raw_symbol == "600519"
    assert record.name == "贵州茅台"
    assert record.security_type == "stock"
    assert record.exchange == "SH"


def test_market_build_validation_accepts_clean_raw_bar() -> None:
    issues = _validate_candidate(
        _raw_bar(),
        existing_symbols={"600519.SH"},
        open_dates_by_exchange={"SH": {date(2026, 7, 17)}},
    )

    assert issues == []


def test_market_build_validation_rejects_missing_security_and_bad_price() -> None:
    issues = _validate_candidate(
        _raw_bar(high=Decimal("1180.00")),
        existing_symbols=set(),
        open_dates_by_exchange={"SH": {date(2026, 7, 17)}},
    )

    assert {issue["issue_type"] for issue in issues} == {
        "SECURITY_NOT_FOUND",
        "PRICE_INVALID",
    }
