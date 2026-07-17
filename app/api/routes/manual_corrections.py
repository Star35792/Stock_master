from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import ManualCorrectionRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/manual-corrections", tags=["manual-corrections"])
service = MarketQueryService()


@router.get("", response_model=Page[ManualCorrectionRead])
def list_manual_corrections(
    db: DatabaseSession,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    adjust_type: Literal["none", "qfq", "hfq"] | None = None,
    field_name: str | None = None,
    corrected_by: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ManualCorrectionRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_manual_corrections(
        db,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        adjust_type=adjust_type,
        field_name=field_name,
        corrected_by=corrected_by,
        limit=limit,
        offset=offset,
    )
