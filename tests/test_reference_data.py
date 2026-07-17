from app.data_sources import SecurityRecord, TradingDayRecord
from app.services.reference_data import ReferenceDataSyncService


class FakeDataSource:
    name = "hithink_finance"

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
            )
        ]

    def fetch_trading_days(self) -> list[TradingDayRecord]:
        return []


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


def test_reference_data_sync_includes_index_securities() -> None:
    data_source = FakeDataSource()
    security_repository = FakeSecurityRepository()
    db = FakeDb()
    service = ReferenceDataSyncService(
        data_source=data_source,  # type: ignore[arg-type]
        security_repository=security_repository,  # type: ignore[arg-type]
        trading_calendar_repository=FakeTradingCalendarRepository(),  # type: ignore[arg-type]
    )

    result = service.sync_hithink_reference_data(db)  # type: ignore[arg-type]

    assert data_source.fetch_security_calls == [
        {"asset_type": "a-share"},
        {"asset_type": "a-share-index", "exchanges": ("SH", "SZ")},
    ]
    assert result.securities_count == 2
    assert {record.security_type for record in security_repository.records} == {"stock", "index"}
    assert db.committed is True
