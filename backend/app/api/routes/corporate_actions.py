from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import CorporateActionRead, Page
from app.services import MarketQueryService

router = APIRouter(prefix="/corporate-actions", tags=["corporate-actions"])
service = MarketQueryService()


@router.get("", response_model=Page[CorporateActionRead])
def list_corporate_actions(
    db: DatabaseSession,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    action_type: Literal[
        "cash_dividend",
        "bonus_share",
        "transfer_share",
        "rights_issue",
        "split",
        "merge",
        "mixed",
    ]
    | None = None,
    source_name: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CorporateActionRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_corporate_actions(
        db,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        action_type=action_type,
        source_name=source_name,
        limit=limit,
        offset=offset,
    )
