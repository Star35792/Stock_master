from datetime import date
from decimal import Decimal

from app.data_sources import DailyBarRecord, HithinkFinanceDataSource
from app.services.hithink_collection import (
    HithinkDailyBarCollectionService,
    _daily_bar_row_hash,
    _infer_security_type,
)


class FakeBatch:
    id = 1


class FakeRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []

    def create_batch(self, db: object, **kwargs: object) -> FakeBatch:
        return FakeBatch()

    def add_daily_bar(self, db: object, row: object) -> None:
        self.rows.append(row)

    def finish_batch(self, db: object, batch: object, **kwargs: object) -> None:
        pass


class FakeSecurityRepository:
    def get_security_types(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {"000001.SH": "index"}


class FakeDataSource:
    name = "hithink_finance"
    source_type = "api"

    def fetch_daily_bars(self, **kwargs: object) -> list[DailyBarRecord]:
        return [
            DailyBarRecord(
                symbol="000001.SH",
                trade_date=date(2026, 7, 17),
                adjust_type="none",
                open=Decimal("3500"),
                high=Decimal("3510"),
                low=Decimal("3490"),
                close=Decimal("3505"),
            )
        ]


class FakeDb:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


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
    assert _infer_security_type("000001.SH") == "index"
    assert _infer_security_type("399001.SZ") == "index"
    assert _infer_security_type("999999.UNKNOWN") == "unknown"


def test_hithink_collection_uses_security_master_type() -> None:
    repository = FakeRepository()
    db = FakeDb()
    service = HithinkDailyBarCollectionService(
        data_source=FakeDataSource(),  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
    )

    result = service.collect_daily_bars(
        db,  # type: ignore[arg-type]
        symbols=["000001.SH"],
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
    )

    assert result.accepted_count == 1
    assert repository.rows[0].security_type == "index"
    assert db.committed is True


def test_hithink_historical_paginates_until_empty() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls: list[int] = []

    def fake_get(path: str, params: dict) -> dict:
        calls.append(params["offset"])
        if params["offset"] == 0:
            return {
                "item": [
                    {
                        "date_ms": 1784217600000,
                        "open_price": 10,
                        "high_price": 11,
                        "low_price": 9,
                        "close_price": 10.5,
                    }
                ]
            }
        return {"item": []}

    source._get = fake_get  # type: ignore[method-assign]
    rows = source.fetch_daily_bars(
        symbol="600519.SH",
        start_date=date(2026, 7, 16),
        end_date=date(2026, 7, 17),
    )

    assert calls == [0, 1]
    assert len(rows) == 1


def test_hithink_historical_uses_server_next_offset() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls: list[int] = []

    def fake_get(path: str, params: dict) -> dict:
        calls.append(params["offset"])
        if params["offset"] == 0:
            return {
                "next_offset": 100,
                "item": [
                    {
                        "date_ms": 1784217600000,
                        "open_price": 10,
                        "high_price": 11,
                        "low_price": 9,
                        "close_price": 10.5,
                    }
                ],
            }
        return {"item": []}

    source._get = fake_get  # type: ignore[method-assign]
    source.fetch_daily_bars(
        symbol="600519.SH",
        start_date=date(2026, 7, 16),
        end_date=date(2026, 7, 17),
    )

    assert calls == [0, 100]
