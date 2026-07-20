from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import Page, SecurityLifecycleRead, SecurityRead
from app.services import MarketQueryService

router = APIRouter(prefix="/securities", tags=["securities"])
service = MarketQueryService()


@router.get("", response_model=Page[SecurityRead])
def list_securities(
    db: DatabaseSession,
    security_type: str | None = None,
    exchange: str | None = None,
    is_active: bool | None = True,
    keyword: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SecurityRead]:
    return service.list_securities(
        db,
        security_type=security_type,
        exchange=exchange,
        is_active=is_active,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )


@router.get("/{symbol}/lifecycle", response_model=SecurityLifecycleRead)
def get_security_lifecycle(
    symbol: str,
    db: DatabaseSession,
    as_of_date: date,
) -> SecurityLifecycleRead:
    result = service.get_security_lifecycle(db, symbol=symbol, as_of_date=as_of_date)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="security not found",
        )
    return result
