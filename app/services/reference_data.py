from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.data_sources import HithinkFinanceDataSource
from app.repositories.market import SecurityRepository, TradingCalendarRepository


@dataclass(frozen=True)
class ReferenceDataSyncResult:
    securities_count: int
    trading_days_count: int


class ReferenceDataSyncService:
    def __init__(
        self,
        *,
        data_source: HithinkFinanceDataSource | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
    ) -> None:
        self.data_source = data_source or HithinkFinanceDataSource()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )

    def sync_hithink_reference_data(self, db: Session) -> ReferenceDataSyncResult:
        securities = [
            *self.data_source.fetch_securities(asset_type="a-share"),
            *self.data_source.fetch_securities(
                asset_type="a-share-index",
                exchanges=("SH", "SZ"),
            ),
        ]
        trading_days = self.data_source.fetch_trading_days()

        securities_count = self.security_repository.upsert_many(
            db,
            records=securities,
            source_name=self.data_source.name,
        )
        trading_days_count = self.trading_calendar_repository.upsert_many(
            db,
            records=trading_days,
            source_name=self.data_source.name,
        )
        db.commit()
        return ReferenceDataSyncResult(
            securities_count=securities_count,
            trading_days_count=trading_days_count,
        )
