from datetime import date
from decimal import Decimal

from app.data_sources import DailyBarRecord
from app.services.hithink_collection import _daily_bar_row_hash, _infer_security_type


def test_hithink_collection_hash_is_stable() -> None:
    record = DailyBarRecord(
        symbol="600519.SH",
        trade_date=date(2026, 7, 17),
        adjust_type="none",
        open=Decimal("1200.00"),
        high=Decimal("1250.00"),
        low=Decimal("1190.00"),
        close=Decimal("1242.47"),
        volume=Decimal("1000"),
        amount=Decimal("1242470"),
        raw_payload={"date_ms": 1784217600000, "close_price": 1242.47},
    )

    assert _daily_bar_row_hash(record) == _daily_bar_row_hash(record)


def test_hithink_collection_infers_security_type() -> None:
    assert _infer_security_type("600519.SH") == "stock"
    assert _infer_security_type("159915.SZ") == "etf"
    assert _infer_security_type("999999.UNKNOWN") == "unknown"
