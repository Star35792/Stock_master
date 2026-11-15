from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.data_sources import DailyBarRecord, HithinkFinanceDataSource
from app.models.raw import ImportBatch, RawDailyBar
from app.repositories.raw import RawDailyBarImportRepository
from app.services.hithink_collection import (
    HithinkDailyBarCollectionService,
    _daily_bar_row_hash,
    _infer_security_type,
)


class FakeBatch:
    id = 1
    request_params: dict[str, object] = {}


class FakeRepository:
    def __init__(self) -> None:
        self.rows: list[object] = []

    def create_batch(self, db: object, **kwargs: object) -> FakeBatch:
        return FakeBatch()

    def load_daily_bar_content_index(self, db: object, **kwargs: object) -> object:
        return object()

    def add_daily_bars_if_changed(
        self, db: object, rows: list[object], *, content_index: object
    ) -> int:
        self.rows.extend(rows)
        return len(rows)

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

    def flush(self) -> None:
        pass


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


def test_hithink_collection_repeated_window_keeps_only_content_versions() -> None:
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE SCHEMA raw")
    ImportBatch.__table__.create(engine)
    RawDailyBar.__table__.create(engine)

    class MutableSource:
        name = "hithink_finance"
        source_type = "api"
        close = Decimal("3505")
        payload_revision = 1

        def fetch_daily_bars(self, **kwargs: object) -> list[DailyBarRecord]:
            return [
                DailyBarRecord(
                    symbol="000001.SH",
                    trade_date=date(2026, 7, 17),
                    adjust_type="none",
                    open=Decimal("3500"),
                    high=Decimal("3510"),
                    low=Decimal("3490"),
                    close=self.close,
                    raw_payload={
                        "vendor_close": str(self.close),
                        "revision": self.payload_revision,
                    },
                )
            ]

    source = MutableSource()
    service = HithinkDailyBarCollectionService(
        data_source=source,  # type: ignore[arg-type]
        repository=RawDailyBarImportRepository(),
        security_repository=FakeSecurityRepository(),  # type: ignore[arg-type]
    )
    captured_at = datetime(2026, 7, 19, tzinfo=ZoneInfo("Asia/Shanghai"))
    with Session(engine) as db:
        def collect():
            return service.collect_daily_bars(
                db,
                symbols=["000001.SH"],
                start_date=date(2026, 7, 17),
                end_date=date(2026, 7, 17),
                clock=lambda: captured_at,
            )

        first = collect()
        repeat = collect()
        source.payload_revision = 2
        payload_revised = collect()
        source.close = Decimal("3506")
        revised = collect()
        assert (first.accepted_count, first.skipped_unchanged) == (1, 0)
        assert (repeat.accepted_count, repeat.skipped_unchanged) == (0, 1)
        assert (payload_revised.accepted_count, payload_revised.skipped_unchanged) == (1, 0)
        assert (revised.accepted_count, revised.skipped_unchanged) == (1, 0)
        assert repeat.row_count == 1
        assert db.scalars(select(RawDailyBar.close).order_by(RawDailyBar.id)).all() == [
            Decimal("3505.0000"), Decimal("3505.0000"), Decimal("3506.0000")
        ]
        batches = db.scalars(select(ImportBatch).order_by(ImportBatch.id)).all()
        assert [batch.accepted_count for batch in batches] == [1, 0, 1, 1]
        assert [batch.row_count for batch in batches] == [1, 1, 1, 1]
    engine.dispose()


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


def _ticker(symbol: str) -> dict:
    code, exchange = symbol.split(".")
    return {
        "thscode": symbol,
        "ticker": code,
        "name": symbol,
        "asset_type": "a-share",
        "exchange": exchange,
        "currency": "CNY",
    }


def test_hithink_securities_stops_when_page_repeats() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls: list[int] = []
    page = [_ticker("600000.SH"), _ticker("000001.SZ")]

    def fake_get(path: str, params: dict) -> dict:
        calls.append(params["offset"])
        return {"item": page}

    source._get = fake_get  # type: ignore[method-assign]
    rows = source.fetch_securities(limit=2)

    assert calls == [0, 2]
    assert [row.symbol for row in rows] == ["600000.SH", "000001.SZ"]


def test_hithink_securities_uses_server_next_offset() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls: list[int] = []

    def fake_get(path: str, params: dict) -> dict:
        calls.append(params["offset"])
        if params["offset"] == 0:
            return {
                "next_offset": 100,
                "item": [_ticker("600000.SH"), _ticker("000001.SZ")],
            }
        return {"item": []}

    source._get = fake_get  # type: ignore[method-assign]
    rows = source.fetch_securities(limit=2)

    assert calls == [0, 100]
    assert len(rows) == 2


def test_hithink_securities_stops_at_total() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls: list[int] = []

    def fake_get(path: str, params: dict) -> dict:
        calls.append(params["offset"])
        return {
            "total": 2,
            "item": [_ticker("600000.SH"), _ticker("000001.SZ")],
        }

    source._get = fake_get  # type: ignore[method-assign]
    rows = source.fetch_securities(limit=2)

    assert calls == [0]
    assert len(rows) == 2
