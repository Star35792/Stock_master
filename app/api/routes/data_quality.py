from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.schemas import DataQualityIssueRead, Page
from app.services import DataQualityQueryService

router = APIRouter(prefix="/data-quality/issues", tags=["data-quality"])
service = DataQualityQueryService()


@router.get("", response_model=Page[DataQualityIssueRead])
def list_data_quality_issues(
    db: DatabaseSession,
    status_filter: Annotated[
        Literal["open", "ignored", "fixed", "false_positive"] | None,
        Query(alias="status"),
    ] = "open",
    severity: Literal["info", "warning", "error", "critical"] | None = None,
    issue_type: str | None = None,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DataQualityIssueRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )

    return service.list_issues(
        db,
        status=status_filter,
        severity=severity,
        issue_type=issue_type,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
