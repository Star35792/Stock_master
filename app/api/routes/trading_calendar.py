from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import Page, TradingCalendarRead
from app.services import MarketQueryService

router = APIRouter(prefix="/trading-calendar", tags=["trading-calendar"])
service = MarketQueryService()


@router.get("", response_model=Page[TradingCalendarRead])
def list_trading_calendar(
    db: DatabaseSession,
    exchange: Literal["SH", "SZ", "BJ", "HK", "UNKNOWN"] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    is_open: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[TradingCalendarRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_trading_calendar(
        db,
        exchange=exchange,
        start_date=start_date,
        end_date=end_date,
        is_open=is_open,
        limit=limit,
        offset=offset,
    )
