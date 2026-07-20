from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import DatabaseSession
from app.schemas.strategy import (
    FactorSelectionBacktestRequest,
    FactorSelectionBacktestResult,
)
from app.services import StrategyResearchService
from app.strategies import FactorScoreSpec

router = APIRouter(prefix="/strategy-research", tags=["strategy-research"])
service = StrategyResearchService()


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
            factor_specs=factor_specs,
            top_n=payload.top_n,
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
        strategy_name="factor_selection",
        start_date=payload.start_date,
        end_date=payload.end_date,
        initial_cash=result.initial_cash,
        final_equity=result.final_equity,
        total_return=result.total_return,
        trade_count=len(result.trades),
        equity_points=len(result.equity_curve),
        position_snapshots=len(result.positions),
    )
