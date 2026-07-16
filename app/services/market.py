from datetime import date

from sqlalchemy.orm import Session

from app.repositories.market import DailyBarRepository, SecurityRepository
from app.schemas import DailyBarRead, Page, SecurityRead


class MarketQueryService:
    def __init__(
        self,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
    ) -> None:
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()

    def list_securities(
        self,
        db: Session,
        *,
        security_type: str | None,
        exchange: str | None,
        is_active: bool | None,
        keyword: str | None,
        limit: int,
        offset: int,
    ) -> Page[SecurityRead]:
        rows, total = self.security_repository.list(
            db,
            security_type=security_type,
            exchange=exchange,
            is_active=is_active,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[SecurityRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_daily_bars(
        self,
        db: Session,
        *,
        symbol: str,
        adjust_type: str,
        start_date: date | None,
        end_date: date | None,
        quality_status: str | None,
        limit: int,
        offset: int,
    ) -> Page[DailyBarRead]:
        rows, total = self.daily_bar_repository.list_by_symbol(
            db,
            symbol=symbol.upper(),
            adjust_type=adjust_type,
            start_date=start_date,
            end_date=end_date,
            quality_status=quality_status,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[DailyBarRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )
