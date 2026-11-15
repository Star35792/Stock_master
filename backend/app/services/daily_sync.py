from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.services.daily_bar_market_builder import DailyBarMarketBuildService, MarketBuildResult
from app.services.hithink_collection import (
    DailyBarCollectionResult,
    HithinkDailyBarCollectionService,
)
from app.services.reference_data import ReferenceDataSyncResult, ReferenceDataSyncService

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _last_completed_date() -> date:
    """The most recent day that has fully passed in Shanghai time.

    Two things go wrong when a sync asks for *today*:

    * the vendor answers with the session as it stands, so a run inside the
      trading hours stores a half-day snapshot whose close is the price at
      that moment;
    * ``date.today()`` reads the **host's** clock, not the exchange's, so on
      a host outside UTC+8 the requested window is silently off by a day.

    A session is only observed once the calendar day is over: a run at 21:00
    still observes the previous day, and only after midnight does the service
    move on to the day that just ended.
    """
    return datetime.now(SHANGHAI).date() - timedelta(days=1)


@dataclass(frozen=True)
class HithinkDailySyncResult:
    reference_data: ReferenceDataSyncResult | None
    collection: DailyBarCollectionResult
    market_build: MarketBuildResult


class HithinkDailySyncService:
    def __init__(
        self,
        *,
        reference_data_service: ReferenceDataSyncService | None = None,
        collection_service: HithinkDailyBarCollectionService | None = None,
        market_build_service: DailyBarMarketBuildService | None = None,
    ) -> None:
        self.reference_data_service = reference_data_service or ReferenceDataSyncService()
        self.collection_service = collection_service or HithinkDailyBarCollectionService()
        self.market_build_service = market_build_service or DailyBarMarketBuildService()

    def sync(
        self,
        db: Session,
        *,
        symbols: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
        days: int = 14,
        adjust_type: str = "none",
        refresh_reference_data: bool = False,
    ) -> HithinkDailySyncResult:
        if not symbols:
            raise ValueError("symbols must not be empty")
        if days < 1:
            raise ValueError("days must be greater than or equal to 1")

        resolved_end_date = end_date or _last_completed_date()
        resolved_start_date = start_date or (resolved_end_date - timedelta(days=days))

        reference_result = None
        if refresh_reference_data:
            reference_result = self.reference_data_service.sync_hithink_reference_data(db)

        collection_result = self.collection_service.collect_daily_bars(
            db,
            symbols=symbols,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            adjust_type=adjust_type,
        )
        market_build_result = self.market_build_service.build_from_raw(
            db,
            symbols=symbols,
            start_date=resolved_start_date,
            end_date=resolved_end_date,
            adjust_type=adjust_type,
            source_name=collection_result.source_name,
        )

        return HithinkDailySyncResult(
            reference_data=reference_result,
            collection=collection_result,
            market_build=market_build_result,
        )
