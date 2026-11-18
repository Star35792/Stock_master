from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import (
    DatabaseSession,
    PositionsDatabaseSession,
    ResearchDatabaseSession,
)
from app.schemas.research_workflow import PipelineRead, WorkbenchRead
from app.services.research_workflow import ResearchWorkflowService

router = APIRouter(prefix="/research-workflow", tags=["research-workflow"])
service = ResearchWorkflowService()


@router.get("/workbench", response_model=WorkbenchRead)
def get_research_workbench(
    db: DatabaseSession,
    positions_db: PositionsDatabaseSession,
    research_db: ResearchDatabaseSession,
    recent_run_limit: int = Query(default=5, ge=1, le=50),
    recent_plan_limit: int = Query(default=10, ge=1, le=100),
) -> WorkbenchRead:
    """研究首页聚合视图：一次返回最近回测、组合草案、持仓账本与告警。"""
    return WorkbenchRead.model_validate(
        service.workbench(
            db,
            positions_db=positions_db,
            research_db=research_db,
            recent_run_limit=recent_run_limit,
            recent_plan_limit=recent_plan_limit,
        )
    )


@router.get("/pipeline", response_model=PipelineRead)
def get_research_pipeline(
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
    run_id: int = Query(..., description="回测 run_id"),
) -> PipelineRead:
    """单次回测的完整研究视图：摘要、净值曲线、绩效、基准与期末持仓。"""
    try:
        return PipelineRead.model_validate(
            service.pipeline(db, run_id=run_id, research_db=research_db)
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
