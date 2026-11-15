from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.services.daily_bar_market_builder import DailyBarMarketBuildService, MarketBuildResult
from app.services.daily_sync import (
    HithinkDailySyncResult,
    HithinkDailySyncService,
    _last_completed_date,
)
from app.services.gap_backfill import GapBackfillResult, MissingDailyBarBackfillService


@dataclass(frozen=True)
class RecentDataCompletionResult:
    primary_sync: HithinkDailySyncResult
    gap_backfill: GapBackfillResult
    gap_market_build: MarketBuildResult | None


class RecentDataCompletionService:
    def __init__(
        self,
        *,
        daily_sync_service: HithinkDailySyncService | None = None,
        gap_backfill_service: MissingDailyBarBackfillService | None = None,
        market_build_service: DailyBarMarketBuildService | None = None,
        today: Callable[[], date] = _last_completed_date,
    ) -> None:
        self.daily_sync_service = daily_sync_service or HithinkDailySyncService()
        self.gap_backfill_service = gap_backfill_service or MissingDailyBarBackfillService()
        self.market_build_service = market_build_service or DailyBarMarketBuildService()
        self.today = today

    def complete(
        self,
        db: Session,
        *,
        symbols: list[str],
        days: int = 14,
        adjust_type: str = "qfq",
        refresh_reference_data: bool = False,
    ) -> RecentDataCompletionResult:
        normalized_symbols = sorted(
            {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        )
        if not normalized_symbols:
            raise ValueError("symbols must not be empty")
        if days < 1:
            raise ValueError("days must be greater than or equal to 1")
        if adjust_type != "qfq":
            raise ValueError("recent data completion currently supports qfq only")

        end_date = self.today()
        start_date = end_date - timedelta(days=days)
        primary_sync = self.daily_sync_service.sync(
            db,
            symbols=normalized_symbols,
            start_date=start_date,
            end_date=end_date,
            days=days,
            adjust_type=adjust_type,
            refresh_reference_data=refresh_reference_data,
        )
        gap_backfill = self.gap_backfill_service.backfill(
            db,
            symbols=normalized_symbols,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
        )
        gap_market_build = None
        if gap_backfill.batch_no:
            gap_market_build = self.market_build_service.build_from_raw(
                db,
                symbols=normalized_symbols,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
            )

        return RecentDataCompletionResult(
            primary_sync=primary_sync,
            gap_backfill=gap_backfill,
            gap_market_build=gap_market_build,
        )
