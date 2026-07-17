from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import FactorDefinitionRead, FactorRunRead, FactorValueRead, Page
from app.services import FactorQueryService

router = APIRouter(prefix="/factors", tags=["factors"])
service = FactorQueryService()


@router.get("/definitions", response_model=Page[FactorDefinitionRead])
def list_factor_definitions(
    db: DatabaseSession,
    name: str | None = None,
    frequency: Literal["daily", "weekly", "monthly"] | None = None,
    is_active: bool | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorDefinitionRead]:
    return service.list_definitions(
        db,
        name=name,
        frequency=frequency,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )


@router.get("/runs", response_model=Page[FactorRunRead])
def list_factor_runs(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    status_: Literal["running", "success", "failed", "partial"] | None = Query(
        default=None,
        alias="status",
    ),
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorRunRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_runs(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        status=status_,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/values", response_model=Page[FactorValueRead])
def list_factor_values(
    db: DatabaseSession,
    factor_name: str | None = None,
    factor_version: str | None = None,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[FactorValueRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_values(
        db,
        factor_name=factor_name,
        factor_version=factor_version,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
