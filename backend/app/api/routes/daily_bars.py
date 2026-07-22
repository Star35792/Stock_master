from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import DailyBarRead, MarketSummaryRead, MissingDailyBarRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/daily-bars", tags=["daily-bars"])
service = MarketQueryService()


@router.get("/missing", response_model=Page[MissingDailyBarRead])
def list_missing_daily_bars(
    db: DatabaseSession,
    start_date: date,
    end_date: date,
    adjust_type: Literal["none", "qfq", "hfq"] = "none",
    symbol: str | None = None,
    security_type: Literal["stock", "etf", "index", "fund", "bond", "unknown"] | None = None,
    exchange: Literal["SH", "SZ", "BJ", "HK", "UNKNOWN"] | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[MissingDailyBarRead]:
    if start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_missing_daily_bars(
        db,
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
        symbol=symbol,
        security_type=security_type,
        exchange=exchange,
        limit=limit,
        offset=offset,
    )


@router.get("/{symbol}/summary", response_model=MarketSummaryRead)
def get_market_summary(
    symbol: str,
    db: DatabaseSession,
    adjust_type: Literal["none", "qfq", "hfq"] = "none",
) -> MarketSummaryRead:
    result = service.get_market_summary(db, symbol=symbol, adjust_type=adjust_type)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="security not found",
        )
    return result


@router.get("/{symbol}/chart", response_model=Page[DailyBarRead])
def list_chart_daily_bars(
    symbol: str,
    db: DatabaseSession,
    adjust_type: Literal["none", "qfq", "hfq"] = "none",
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=20, le=1000)] = 320,
) -> Page[DailyBarRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_chart_daily_bars(
        db,
        symbol=symbol,
        adjust_type=adjust_type,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


@router.get("/{symbol}", response_model=Page[DailyBarRead])
def list_daily_bars(
    symbol: str,
    db: DatabaseSession,
    adjust_type: Literal["none", "qfq", "hfq"] = "none",
    start_date: date | None = None,
    end_date: date | None = None,
    quality_status: Literal[
        "consumable", "trusted", "suspect", "rejected", "manual_fixed"
    ]
    | None = ("trusted"),
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
