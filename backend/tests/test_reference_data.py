from datetime import date

from app.data_sources import SecurityRecord, TradingDayRecord
from app.services.reference_data import ReferenceDataSyncService


class FakeDataSource:
    name = "hithink_finance"
    source_type = "api"

    def __init__(self) -> None:
        self.fetch_security_calls: list[dict] = []

    def fetch_securities(self, **kwargs: object) -> list[SecurityRecord]:
        self.fetch_security_calls.append(kwargs)
        if kwargs["asset_type"] == "a-share-index":
            return [
                SecurityRecord(
                    symbol="000001.SH",
                    raw_symbol="000001",
                    name="上证指数",
                    security_type="index",
                    exchange="SH",
                )
            ]
        return [
            SecurityRecord(
                symbol="600519.SH",
                raw_symbol="600519",
                name="贵州茅台",
                security_type="stock",
                exchange="SH",
                list_date=date(2001, 8, 27),
            )
        ]

    def fetch_trading_days(self) -> list[TradingDayRecord]:
        return [TradingDayRecord(exchange="SH", trade_date=date(2026, 9, 22))]


class FakeRawRepository:
    def __init__(self) -> None:
        self.finished = False

    def create_batch(self, db: object, **kwargs: object):
        class Batch:
            id = 1
            request_params = {}

        return Batch()

    def finish_batch(self, db: object, batch: object, **kwargs: object) -> None:
        self.finished = True


class FakeSecurityRepository:
    def __init__(self) -> None:
        self.records: list[SecurityRecord] = []

    def upsert_many(
        self,
        db: object,
        *,
        records: list[SecurityRecord],
        source_name: str,
    ) -> int:
        self.records = records
        return len(records)


class FakeTradingCalendarRepository:
    def upsert_many(
        self,
        db: object,
        *,
        records: list[TradingDayRecord],
        source_name: str,
    ) -> int:
        return len(records)


class FakeDb:
    def __init__(self) -> None:
        self.committed = False

    def commit(self) -> None:
        self.committed = True

    def add(self, item: object) -> None:
        pass


def test_reference_data_sync_includes_index_securities() -> None:
    data_source = FakeDataSource()
    security_repository = FakeSecurityRepository()
    db = FakeDb()
    service = ReferenceDataSyncService(
        data_source=data_source,  # type: ignore[arg-type]
        security_repository=security_repository,  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        raw_repository=FakeRawRepository(),  # type: ignore[arg-type]
    )

    result = service.sync_hithink_reference_data(db)  # type: ignore[arg-type]

    assert data_source.fetch_security_calls == [
        {"asset_type": "a-share"},
        {"asset_type": "a-share-index", "exchanges": ("SH", "SZ")},
    ]
    assert result.securities_count == 2
    assert {record.security_type for record in security_repository.records} == {"stock", "index"}
    assert db.committed is True


def test_reference_sync_merges_intermittent_listing_dates() -> None:
    class IntermittentSource(FakeDataSource):
        def __init__(self) -> None:
            super().__init__()
            self.stock_calls = 0

        def fetch_securities(self, **kwargs: object) -> list[SecurityRecord]:
            self.fetch_security_calls.append(kwargs)
            if kwargs["asset_type"] == "a-share-index":
                return []
            self.stock_calls += 1
            return [
                SecurityRecord(
                    symbol="600519.SH", raw_symbol="600519", name="贵州茅台",
                    security_type="stock", exchange="SH",
                    list_date=date(2001, 8, 27) if self.stock_calls >= 3 else None,
                )
            ]

    source = IntermittentSource()
    repo = FakeSecurityRepository()
    service = ReferenceDataSyncService(
        data_source=source,  # type: ignore[arg-type]
        security_repository=repo,  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        raw_repository=FakeRawRepository(),  # type: ignore[arg-type]
    )
    service.sync_hithink_reference_data(FakeDb())  # type: ignore[arg-type]
    assert source.stock_calls == 3
    assert repo.records[0].list_date == date(2001, 8, 27)


def test_reference_sync_keeps_unknown_listing_date_without_failing_daily_sync() -> None:
    class MissingDatesSource(FakeDataSource):
        def fetch_securities(self, **kwargs: object) -> list[SecurityRecord]:
            records = super().fetch_securities(**kwargs)
            if kwargs["asset_type"] == "a-share":
                return [SecurityRecord(
                    symbol=record.symbol, raw_symbol=record.raw_symbol,
                    name=record.name, security_type=record.security_type,
                    exchange=record.exchange, list_date=None,
                ) for record in records]
            return records

    repo = FakeSecurityRepository()
    service = ReferenceDataSyncService(
        data_source=MissingDatesSource(),  # type: ignore[arg-type]
        security_repository=repo,  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
        raw_repository=FakeRawRepository(),  # type: ignore[arg-type]
    )

    result = service.sync_hithink_reference_data(FakeDb())  # type: ignore[arg-type]

    assert result.securities_count == 2
    assert repo.records[0].list_date is None
