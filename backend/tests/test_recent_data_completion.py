from datetime import date

import pytest

from app.services import (
    DailyBarCollectionResult,
    GapBackfillResult,
    HithinkDailySyncResult,
    MarketBuildResult,
    RecentDataCompletionService,
)


class FakeDailySyncService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict] = []

    def sync(self, db: object, **kwargs: object) -> HithinkDailySyncResult:
        self.events.append("primary_sync")
        self.calls.append(kwargs)
        return HithinkDailySyncResult(
            reference_data=None,
            collection=DailyBarCollectionResult(
                batch_no="primary-1",
                source_name="hithink_finance",
                status="success",
                row_count=1,
                accepted_count=1,
                rejected_count=0,
                error_message=None,
            ),
            market_build=MarketBuildResult(
                candidates_count=1,
                trusted_count=1,
                rejected_count=0,
                issue_count=0,
            ),
        )


class FakeGapBackfillService:
    def __init__(self, events: list[str], result: GapBackfillResult) -> None:
        self.events = events
        self.result = result
        self.calls: list[dict] = []

    def backfill(self, db: object, **kwargs: object) -> GapBackfillResult:
        self.events.append("gap_backfill")
        self.calls.append(kwargs)
        return self.result


class FakeMarketBuildService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls: list[dict] = []

    def build_from_raw(self, db: object, **kwargs: object) -> MarketBuildResult:
        self.events.append("gap_market_build")
        self.calls.append(kwargs)
        return MarketBuildResult(
            candidates_count=1,
            trusted_count=1,
            rejected_count=0,
            issue_count=0,
        )


def test_recent_data_completion_syncs_backfills_and_rebuilds_market() -> None:
    events: list[str] = []
    daily_sync = FakeDailySyncService(events)
    gap_backfill = FakeGapBackfillService(
        events,
        GapBackfillResult(
            batch_no="gap-1",
            expected_rows=2,
            fetched_rows=2,
            inserted_rows=2,
            skipped_rows=0,
        ),
    )
    market_build = FakeMarketBuildService(events)
    service = RecentDataCompletionService(
        daily_sync_service=daily_sync,  # type: ignore[arg-type]
        gap_backfill_service=gap_backfill,  # type: ignore[arg-type]
        market_build_service=market_build,  # type: ignore[arg-type]
        today=lambda: date(2026, 7, 18),
    )

    result = service.complete(
        object(),  # type: ignore[arg-type]
        symbols=["600519.sh", " 000001.SZ "],
        days=7,
        refresh_reference_data=True,
    )

    assert events == ["primary_sync", "gap_backfill", "gap_market_build"]
    assert daily_sync.calls == [
        {
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": date(2026, 7, 11),
            "end_date": date(2026, 7, 18),
            "days": 7,
            "adjust_type": "qfq",
            "refresh_reference_data": True,
        }
    ]
    assert gap_backfill.calls == [
        {
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": date(2026, 7, 11),
            "end_date": date(2026, 7, 18),
            "adjust_type": "qfq",
        }
    ]
    assert market_build.calls == [
        {
            "symbols": ["000001.SZ", "600519.SH"],
            "start_date": date(2026, 7, 11),
            "end_date": date(2026, 7, 18),
            "adjust_type": "qfq",
        }
    ]
    assert result.gap_market_build is not None
    assert result.gap_market_build.trusted_count == 1


def test_recent_data_completion_skips_gap_market_build_when_no_gaps() -> None:
    events: list[str] = []
    service = RecentDataCompletionService(
        daily_sync_service=FakeDailySyncService(events),  # type: ignore[arg-type]
        gap_backfill_service=FakeGapBackfillService(
            events,
            GapBackfillResult(
                batch_no=None,
                expected_rows=0,
                fetched_rows=0,
                inserted_rows=0,
                skipped_rows=0,
            ),
        ),  # type: ignore[arg-type]
        market_build_service=FakeMarketBuildService(events),  # type: ignore[arg-type]
        today=lambda: date(2026, 7, 18),
    )

    result = service.complete(
        object(),  # type: ignore[arg-type]
        symbols=["600519.SH"],
    )

    assert events == ["primary_sync", "gap_backfill"]
    assert result.gap_market_build is None


def test_recent_data_completion_requires_qfq() -> None:
    service = RecentDataCompletionService()

    with pytest.raises(ValueError, match="qfq"):
        service.complete(
            object(),  # type: ignore[arg-type]
            symbols=["600519.SH"],
            adjust_type="none",
        )
