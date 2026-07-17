from datetime import date

import pytest

from app.services import (
    DailyBarCollectionResult,
    HithinkDailySyncService,
    MarketBuildResult,
    ReferenceDataSyncResult,
)


class FakeReferenceService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.called = False

    def sync_hithink_reference_data(self, db: object) -> ReferenceDataSyncResult:
        self.events.append("reference")
        self.called = True
        return ReferenceDataSyncResult(securities_count=2, trading_days_count=3)


class FakeCollectionService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict] = []

    def collect_daily_bars(self, db: object, **kwargs: object) -> DailyBarCollectionResult:
        self.events.append("collection")
        self.calls.append(kwargs)
        return DailyBarCollectionResult(
            batch_no="batch-1",
            source_name="hithink_finance",
            status="success",
            row_count=2,
            accepted_count=2,
            rejected_count=0,
            error_message=None,
        )


class FakeMarketBuildService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict] = []

    def build_from_raw(self, db: object, **kwargs: object) -> MarketBuildResult:
        self.events.append("market_build")
        self.calls.append(kwargs)
        return MarketBuildResult(
            candidates_count=2,
            trusted_count=2,
            rejected_count=0,
            issue_count=0,
        )


def test_daily_sync_orchestrates_collection_and_market_build() -> None:
    events: list[str] = []
    reference = FakeReferenceService(events)
    collection = FakeCollectionService(events)
    market_build = FakeMarketBuildService(events)
    service = HithinkDailySyncService(
        reference_data_service=reference,  # type: ignore[arg-type]
        collection_service=collection,  # type: ignore[arg-type]
        market_build_service=market_build,  # type: ignore[arg-type]
    )

    result = service.sync(
        object(),  # type: ignore[arg-type]
        symbols=["600519.SH"],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 17),
        refresh_reference_data=True,
    )

    assert result.reference_data == ReferenceDataSyncResult(
        securities_count=2,
        trading_days_count=3,
    )
    assert events == ["reference", "collection", "market_build"]
    assert collection.calls == [
        {
            "symbols": ["600519.SH"],
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 17),
            "adjust_type": "none",
        }
    ]
    assert market_build.calls == [
        {
            "symbols": ["600519.SH"],
            "start_date": date(2026, 7, 1),
            "end_date": date(2026, 7, 17),
            "adjust_type": "none",
            "source_name": "hithink_finance",
        }
    ]
    assert result.market_build.trusted_count == 2


def test_daily_sync_can_skip_reference_data() -> None:
    events: list[str] = []
    reference = FakeReferenceService(events)
    service = HithinkDailySyncService(
        reference_data_service=reference,  # type: ignore[arg-type]
        collection_service=FakeCollectionService(events),  # type: ignore[arg-type]
        market_build_service=FakeMarketBuildService(events),  # type: ignore[arg-type]
    )

    result = service.sync(
        object(),  # type: ignore[arg-type]
        symbols=["600519.SH"],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 17),
    )

    assert result.reference_data is None
    assert reference.called is False
    assert events == ["collection", "market_build"]


def test_daily_sync_requires_symbols() -> None:
    service = HithinkDailySyncService()

    with pytest.raises(ValueError, match="symbols"):
        service.sync(object(), symbols=[])  # type: ignore[arg-type]


def test_daily_sync_requires_positive_days() -> None:
    service = HithinkDailySyncService()

    with pytest.raises(ValueError, match="days"):
        service.sync(object(), symbols=["600519.SH"], days=0)  # type: ignore[arg-type]
