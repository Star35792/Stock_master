from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import DatabaseSession
from app.schemas.strategy import (
    BatchFactorSelectionBacktestItem,
    BatchFactorSelectionBacktestRequest,
    FactorSelectionBacktestRequest,
    FactorSelectionBacktestResult,
)
from app.services import StrategyResearchService
from app.strategies import FactorScoreSpec

router = APIRouter(prefix="/strategy-research", tags=["strategy-research"])
service = StrategyResearchService()


@router.post(
    "/factor-selection/backtests/batch",
    response_model=list[BatchFactorSelectionBacktestItem],
)
def run_factor_selection_backtests_batch(
    payload: BatchFactorSelectionBacktestRequest,
    db: DatabaseSession,
) -> list[BatchFactorSelectionBacktestItem]:
    rolling_runs = [item for item in payload.runs if item.validation_mode == "rolling"]
    if rolling_runs:
        if len(rolling_runs) != len(payload.runs):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="rolling batches cannot mix validation modes",
            )
        test_starts = [item.start_date for item in payload.runs]
        if test_starts != sorted(test_starts) or len(set(test_starts)) != len(test_starts):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="rolling windows must have strictly increasing test start dates",
            )
    results: list[BatchFactorSelectionBacktestItem] = []
    for index, item in enumerate(payload.runs):
        try:
            result = run_factor_selection_backtest(item, db)
            results.append(
                BatchFactorSelectionBacktestItem(index=index, status="success", result=result)
            )
        except HTTPException as exc:
            results.append(
                BatchFactorSelectionBacktestItem(
                    index=index, status="failed", error_message=str(exc.detail)
                )
            )
    return results


@router.post(
    "/factor-selection/backtests",
    response_model=FactorSelectionBacktestResult,
)
def run_factor_selection_backtest(
    payload: FactorSelectionBacktestRequest,
    db: DatabaseSession,
) -> FactorSelectionBacktestResult:
    if payload.start_date > payload.end_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be earlier than or equal to end_date",
        )
    if payload.validation_mode != "none":
        if payload.training_start_date is None or payload.training_end_date is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="training dates are required for validation runs",
            )
        if payload.training_start_date > payload.training_end_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="training_start_date must be earlier than or equal to training_end_date",
            )
        if payload.training_end_date >= payload.start_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="training period must end before the backtest period",
            )

    factor_specs = [
        FactorScoreSpec(
            factor_name=spec.factor_name,
            factor_version=spec.factor_version,
            weight=spec.weight,
            higher_is_better=spec.higher_is_better,
        )
        for spec in payload.factor_specs
    ]
    try:
        persisted = service.run_factor_selection(
            db,
            name=payload.name,
            strategy_name=payload.strategy_name,
            factor_specs=factor_specs,
            top_n=payload.top_n,
            signal_lag=payload.signal_lag,
            rebalance_rule=payload.rebalance_rule,
            holding_period=payload.holding_period,
            benchmark=payload.benchmark,
            allowed_security_types=set(payload.allowed_security_types),
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust_type=payload.adjust_type,
            initial_cash=payload.initial_cash,
            commission_rate=payload.commission_rate,
            slippage_bps=payload.slippage_bps,
            lot_size=payload.lot_size,
            code_version=payload.code_version,
            data_version=payload.data_version,
            validation_mode=payload.validation_mode,
            training_start_date=payload.training_start_date,
            training_end_date=payload.training_end_date,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    result = persisted.result
    return FactorSelectionBacktestResult(
        run_id=persisted.run_id,
        name=payload.name,
        strategy_name=payload.strategy_name,
        start_date=payload.start_date,
        end_date=payload.end_date,
        initial_cash=result.initial_cash,
        final_equity=result.final_equity,
        total_return=result.total_return,
        trade_count=len(result.trades),
        equity_points=len(result.equity_curve),
        position_snapshots=len(result.positions),
    )
