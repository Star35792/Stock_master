from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import AdjustmentFactorRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/adjustment-factors", tags=["adjustment-factors"])
service = MarketQueryService()


@router.get("", response_model=Page[AdjustmentFactorRead])
def list_adjustment_factors(
    db: DatabaseSession,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    adjust_type: Literal["qfq", "hfq"] | None = None,
    source_name: str | None = None,
    version: str | None = None,
    quality_status: Literal["trusted", "suspect", "rejected"] | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[AdjustmentFactorRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_adjustment_factors(
        db,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
        source_name=source_name,
        version=version,
        quality_status=quality_status,
        limit=limit,
        offset=offset,
    )
