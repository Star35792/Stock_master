from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import DatabaseSession
from app.schemas import Page, SecurityRead
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
