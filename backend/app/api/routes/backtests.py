from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from app.api.dependencies import DatabaseSession, ResearchDatabaseSession
from app.schemas import Page
from app.schemas.research_validation import (
    ResearchValidationReportRead,
    ResearchValidationReportRequest,
)
from app.schemas.strategy import (
    BacktestBenchmarkRead,
    BacktestComparisonItem,
    BacktestComparisonRequest,
    BacktestDriftRead,
    BacktestDriftRequest,
    BacktestEquityRead,
    BacktestPerformanceAnalyzeRequest,
    BacktestPerformanceRead,
    BacktestPositionRead,
    BacktestRunRead,
    BacktestTradeRead,
)
from app.services import BacktestQueryService
from app.services.research_validation import ResearchValidationReportService

router = APIRouter(prefix="/backtests", tags=["backtests"])
service = BacktestQueryService()
validation_report_service = ResearchValidationReportService()


@router.post(
    "/runs/validation-report",
    response_model=ResearchValidationReportRead,
)
def build_research_validation_report(
    research_db: ResearchDatabaseSession,
    payload: ResearchValidationReportRequest,
) -> ResearchValidationReportRead:
    try:
        return validation_report_service.build_report(
            research_db, run_ids=payload.run_ids
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.post("/runs/drift", response_model=BacktestDriftRead)
def detect_backtest_drift(
    research_db: ResearchDatabaseSession,
    payload: BacktestDriftRequest,
) -> BacktestDriftRead:
    try:
        return service.detect_drift(
            research_db,
            baseline_run_id=payload.baseline_run_id,
            candidate_run_ids=payload.candidate_run_ids,
            return_tolerance=payload.return_tolerance,
            drawdown_tolerance=payload.drawdown_tolerance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/runs/compare", response_model=list[BacktestComparisonItem])
def compare_backtest_runs(
    research_db: ResearchDatabaseSession,
    payload: BacktestComparisonRequest,
) -> list[BacktestComparisonItem]:
    try:
        return service.compare_runs(research_db, run_ids=payload.run_ids)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


def _raise_not_found(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


@router.get("/runs", response_model=Page[BacktestRunRead])
def list_backtest_runs(
    research_db: ResearchDatabaseSession,
    strategy_name: str | None = None,
    status_: Literal["running", "success", "failed"] | None = Query(
        default=None,
        alias="status",
    ),
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[BacktestRunRead]:
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_runs(
        research_db,
        strategy_name=strategy_name,
        status=status_,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}", response_model=BacktestRunRead)
def get_backtest_run(
    research_db: ResearchDatabaseSession, run_id: int
) -> BacktestRunRead:
    run = service.get_run(research_db, run_id=run_id)
    if run is None:
        _raise_not_found("backtest run not found")
    return run


@router.get("/runs/{run_id}/trades", response_model=Page[BacktestTradeRead])
def list_backtest_trades(
    research_db: ResearchDatabaseSession,
    run_id: int,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[BacktestTradeRead]:
    if service.get_run(research_db, run_id=run_id) is None:
        _raise_not_found("backtest run not found")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_trades(
        research_db,
        run_id=run_id,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/equity", response_model=Page[BacktestEquityRead])
def list_backtest_equity(
    research_db: ResearchDatabaseSession,
    run_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[BacktestEquityRead]:
    if service.get_run(research_db, run_id=run_id) is None:
        _raise_not_found("backtest run not found")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_equity(
        research_db,
        run_id=run_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/positions", response_model=Page[BacktestPositionRead])
def list_backtest_positions(
    research_db: ResearchDatabaseSession,
    run_id: int,
    symbol: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[BacktestPositionRead]:
    if service.get_run(research_db, run_id=run_id) is None:
        _raise_not_found("backtest run not found")
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    return service.list_positions(
        research_db,
        run_id=run_id,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )


@router.get("/runs/{run_id}/performance", response_model=BacktestPerformanceRead)
def get_backtest_performance(
    research_db: ResearchDatabaseSession,
    run_id: int,
) -> BacktestPerformanceRead:
    performance = service.get_performance(research_db, run_id=run_id)
    if performance is None:
        _raise_not_found("backtest performance not found")
    return performance


@router.get("/runs/{run_id}/benchmark", response_model=BacktestBenchmarkRead)
def get_backtest_benchmark(
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
    run_id: int,
) -> BacktestBenchmarkRead:
    try:
        return service.get_benchmark_comparison(db, run_id=run_id)
    except ValueError as exc:
        detail = str(exc)
        code = 404 if detail == "backtest run not found" else 422
        raise HTTPException(status_code=code, detail=detail) from exc


@router.post("/runs/{run_id}/performance", response_model=BacktestPerformanceRead)
def analyze_backtest_performance(
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
    run_id: int,
    payload: BacktestPerformanceAnalyzeRequest,
) -> BacktestPerformanceRead:
    if service.get_run(research_db, run_id=run_id) is None:
        _raise_not_found("backtest run not found")
    style_factors = {
        style_name: (ref.factor_name, ref.factor_version)
        for style_name, ref in payload.style_factors.items()
    }
    try:
        performance = service.analyze_performance(
            db,
            research_db=research_db,
            run_id=run_id,
            code_version=payload.code_version,
            style_factors=style_factors,
        )
        research_db.commit()
        return performance
    except ValueError as exc:
        research_db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
