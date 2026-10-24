from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession, ResearchDatabaseSession
from app.schemas.operations import (
    OperationsAlertSummary,
    OperationsFailureDetail,
    OperationsFailureReport,
    OperationsOverview,
    OperationsRerunPlan,
)
from app.services import OperationsOverviewService

router = APIRouter(prefix="/operations", tags=["operations"])
service = OperationsOverviewService()


@router.get("/overview", response_model=OperationsOverview)
def get_operations_overview(
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> OperationsOverview:
    return service.get_overview(db, limit=limit, research_db=research_db)


@router.get("/alerts", response_model=OperationsAlertSummary)
def get_operations_alerts(
    db: DatabaseSession, research_db: ResearchDatabaseSession
) -> OperationsAlertSummary:
    return service.get_alerts(db, research_db=research_db)


@router.get("/failures", response_model=OperationsFailureReport)
def get_operations_failures(
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> OperationsFailureReport:
    return service.get_failures(db, limit=limit, research_db=research_db)


@router.get("/failures/{failure_type}/{failure_id}", response_model=OperationsFailureDetail)
def get_operations_failure_detail(
    failure_type: Literal[
        "data_quality_issue",
        "job",
        "factor_run",
        "backtest_run",
        "paper_order",
    ],
    failure_id: int,
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
) -> OperationsFailureDetail:
    detail = service.get_failure_detail(
        db,
        research_db=research_db,
        failure_type=failure_type,
        failure_id=failure_id,
    )
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="operations failure not found",
        )
    return detail


@router.get(
    "/failures/{failure_type}/{failure_id}/rerun-plan",
    response_model=OperationsRerunPlan,
)
def get_operations_failure_rerun_plan(
    failure_type: Literal[
        "data_quality_issue",
        "job",
        "factor_run",
        "backtest_run",
        "paper_order",
    ],
    failure_id: int,
    db: DatabaseSession,
) -> OperationsRerunPlan:
    plan = service.get_rerun_plan(
        db,
        failure_type=failure_type,
        failure_id=failure_id,
    )
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="operations failure not found",
        )
    return plan
