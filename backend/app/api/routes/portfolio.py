from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, status

from app.api.dependencies import DatabaseSession
from app.config.settings import settings
from app.portfolio import PortfolioAsset, PortfolioConstraints, PortfolioRiskBudget
from app.schemas.portfolio import (
    PortfolioPlanCreateRequest,
    PortfolioPlanRead,
    PortfolioRiskReadinessRead,
)
from app.schemas.risk import RiskEvaluationCreateRequest, RiskEvaluationRead
from app.services.portfolio import (
    PortfolioConstructionService,
    PortfolioIdempotencyConflict,
)
from app.services.portfolio_readiness import (
    PaperAccountNotFound,
    PortfolioPlanNotFound,
    PortfolioRiskReadinessService,
)
from app.services.risk_evaluation import (
    BoundRiskEvaluationService,
    RiskEvaluationConflict,
    RiskEvaluationResourceNotFound,
    RiskEvaluationValidationError,
)

router = APIRouter(prefix="/portfolio/plans", tags=["portfolio"])
service = PortfolioConstructionService()
readiness_service = PortfolioRiskReadinessService()
risk_evaluation_service = BoundRiskEvaluationService()


@router.post("", response_model=PortfolioPlanRead, status_code=status.HTTP_201_CREATED)
def create_portfolio_plan(
    db: DatabaseSession,
    payload: PortfolioPlanCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> PortfolioPlanRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )

    input_snapshot = payload.model_dump(mode="json")
    input_snapshot["input_source"] = "operator_provided"
    try:
        result = service.build(
            db,
            name=payload.name,
            as_of_date=payload.as_of_date,
            assets=[PortfolioAsset(**asset.model_dump()) for asset in payload.assets],
            cash=payload.cash,
            constraints=PortfolioConstraints(
                **payload.constraints.model_dump(exclude={"risk_budget"}),
                risk_budget=(
                    PortfolioRiskBudget(**payload.constraints.risk_budget.model_dump())
                    if payload.constraints.risk_budget is not None
                    else None
                ),
            ),
            code_version=settings.app_code_version,
            idempotency_key=normalized_key,
            input_snapshot=input_snapshot,
        )
        db.commit()
        return result
    except PortfolioIdempotencyConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/{plan_id}", response_model=PortfolioPlanRead)
def get_portfolio_plan(db: DatabaseSession, plan_id: int) -> PortfolioPlanRead:
    result = service.get_plan(db, plan_id=plan_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="portfolio plan not found",
        )
    return result


@router.get("/{plan_id}/risk-readiness", response_model=PortfolioRiskReadinessRead)
def get_portfolio_risk_readiness(
    db: DatabaseSession,
    plan_id: int,
    paper_account_id: Annotated[int, Query(gt=0)],
) -> PortfolioRiskReadinessRead:
    try:
        return readiness_service.check(
            db,
            plan_id=plan_id,
            paper_account_id=paper_account_id,
        )
    except (PortfolioPlanNotFound, PaperAccountNotFound) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/{plan_id}/risk-evaluations",
    response_model=RiskEvaluationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_portfolio_risk_evaluation(
    db: DatabaseSession,
    plan_id: int,
    payload: RiskEvaluationCreateRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=100),
    ],
) -> RiskEvaluationRead:
    normalized_key = idempotency_key.strip()
    if not normalized_key:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Idempotency-Key must not be blank",
        )
    try:
        result = risk_evaluation_service.evaluate(
            db,
            plan_id=plan_id,
            paper_account_id=payload.paper_account_id,
            equity_snapshot_id=payload.equity_snapshot_id,
            idempotency_key=normalized_key,
        )
        db.commit()
        return result
    except RiskEvaluationResourceNotFound as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except RiskEvaluationConflict as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except RiskEvaluationValidationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        raise
