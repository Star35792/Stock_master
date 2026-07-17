from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import DailyBarSourceCompareRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/daily-bar-source-compares", tags=["daily-bar-source-compares"])
service = MarketQueryService()


@router.get("", response_model=Page[DailyBarSourceCompareRead])
def list_daily_bar_source_compares(
    db: DatabaseSession,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    adjust_type: Literal["none", "qfq", "hfq"] | None = None,
    is_conflict: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DailyBarSourceCompareRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_source_compares(
        db,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
        is_conflict=is_conflict,
        limit=limit,
        offset=offset,
    )
