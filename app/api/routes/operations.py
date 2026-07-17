from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import DatabaseSession
from app.schemas.operations import OperationsOverview
from app.services import OperationsOverviewService

router = APIRouter(prefix="/operations", tags=["operations"])
service = OperationsOverviewService()


@router.get("/overview", response_model=OperationsOverview)
def get_operations_overview(
    db: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
) -> OperationsOverview:
    return service.get_overview(db, limit=limit)
