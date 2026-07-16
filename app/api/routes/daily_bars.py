from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import DailyBarRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/daily-bars", tags=["daily-bars"])
service = MarketQueryService()


@router.get("/{symbol}", response_model=Page[DailyBarRead])
def list_daily_bars(
    symbol: str,
    db: DatabaseSession,
    adjust_type: Literal["none", "qfq", "hfq"] = "none",
    start_date: date | None = None,
    end_date: date | None = None,
    quality_status: Literal["trusted", "suspect", "rejected", "manual_fixed"] | None = (
        "trusted"
    ),
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DailyBarRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_daily_bars(
        db,
        symbol=symbol,
        adjust_type=adjust_type,
        start_date=start_date,
        end_date=end_date,
        quality_status=quality_status,
        limit=limit,
        offset=offset,
    )
