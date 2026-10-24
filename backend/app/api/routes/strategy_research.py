from fastapi import APIRouter, HTTPException, status

from app.api.dependencies import DatabaseSession, ResearchDatabaseSession
from app.portfolio import PortfolioConstraints
from app.schemas.price_value import PriceValueRequest
from app.schemas.price_value_backtest import PriceValueBacktestRequest, PriceValueBacktestResponse
from app.schemas.price_value_result import PriceValueResult
from app.schemas.strategy import (
    BacktestPortfolioConstraintsRequest,
    BatchFactorSelectionBacktestItem,
    BatchFactorSelectionBacktestRequest,
    FactorSelectionBacktestRequest,
    FactorSelectionBacktestResult,
    RollingFactorSelectionBacktestRequest,
    RollingWindowRead,
    RollingWindowRequest,
)
from app.services import StrategyResearchService
from app.services.backtest import BacktestDataChangedError
from app.services.price_value_backtest import PriceValueBacktestService
from app.services.price_value_research import PriceValueResearchService
from app.services.price_value_screen import PriceValueScreenService
from app.strategies import FactorScoreSpec

router = APIRouter(prefix="/strategy-research", tags=["strategy-research"])
service = StrategyResearchService()
price_value_service = PriceValueResearchService()
price_value_backtest_service = PriceValueBacktestService()
price_value_screen_service = PriceValueScreenService()


def _to_portfolio_constraints(
    request: BacktestPortfolioConstraintsRequest | None,
) -> PortfolioConstraints | None:
    if request is None:
        return None
    return PortfolioConstraints(
        max_symbol_weight=request.max_symbol_weight,
        max_industry_weight=request.max_industry_weight,
        cash_reserve_rate=request.cash_reserve_rate,
        security_type_budgets=request.security_type_budgets,
    )


@router.get("/price-value/screen/latest")
def latest_price_value_screen(db: DatabaseSession) -> dict:
    result = price_value_screen_service.latest(db)
    if result is None:
        raise HTTPException(status_code=404, detail="price/value screen has not run")
    return result


@router.get("/price-value/profit-status")
def price_value_profit_status(db: DatabaseSession) -> dict:
    return price_value_screen_service.profit_status(db)


@router.post("/price-value/backtests", response_model=PriceValueBacktestResponse)
def run_price_value_backtest(
    payload: PriceValueBacktestRequest,
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
) -> PriceValueBacktestResponse:
    try:
        result = price_value_backtest_service.run(db, payload, research_db=research_db)
        research_db.commit()
        return result
    except BacktestDataChangedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise


@router.get("/price-value/backtests/{run_id}", response_model=PriceValueBacktestResponse)
def get_price_value_backtest(run_id: int, db: DatabaseSession) -> PriceValueBacktestResponse:
    try:
        return price_value_backtest_service.get(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/price-value/evaluate", response_model=PriceValueResult)
def evaluate_price_value(payload: PriceValueRequest, db: DatabaseSession) -> PriceValueResult:
    """Evaluate explicit research assumptions without writing data or trading."""
    try:
        return price_value_service.evaluate(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/rolling-windows", response_model=list[RollingWindowRead])
def generate_rolling_windows(
    payload: RollingWindowRequest,
    db: DatabaseSession,
) -> list[RollingWindowRead]:
    try:
        return service.generate_rolling_windows(
            db,
            exchange=payload.exchange,
            start_date=payload.start_date,
            end_date=payload.end_date,
            training_days=payload.training_days,
            test_days=payload.test_days,
            step_days=payload.step_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/factor-selection/backtests/batch",
    response_model=list[BatchFactorSelectionBacktestItem],
)
def run_factor_selection_backtests_batch(
    payload: BatchFactorSelectionBacktestRequest,
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
) -> list[BatchFactorSelectionBacktestItem]:
    rolling_runs = [item for item in payload.runs if item.validation_mode == "rolling"]
    if rolling_runs:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "rolling execution must use POST /strategy-research/rolling-backtests; "
                "the batch endpoint does not accept validation_mode=rolling"
            ),
        )
    results: list[BatchFactorSelectionBacktestItem] = []
    for index, item in enumerate(payload.runs):
        try:
            result = run_factor_selection_backtest(item, db, research_db)
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
    research_db: ResearchDatabaseSession,
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
            rebalance_interval=payload.rebalance_interval,
            portfolio_constraints=_to_portfolio_constraints(
                payload.portfolio_constraints
            ),
            benchmark=payload.benchmark,
            allowed_security_types=set(payload.allowed_security_types),
            coverage_ratio=payload.coverage_ratio,
            weighting_mode=payload.weighting_mode,
            minimum_score_policy=payload.minimum_score_policy,
            minimum_score=payload.minimum_score,
            invested_weight=payload.invested_weight,
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust_type=payload.adjust_type,
            initial_cash=payload.initial_cash,
            commission_rate=payload.commission_rate,
            slippage_bps=payload.slippage_bps,
            lot_size=payload.lot_size,
            fee_model_version=payload.fee_model_version,
            min_commission=payload.min_commission,
            sell_stamp_duty_rate=payload.sell_stamp_duty_rate,
            transfer_fee_rate=payload.transfer_fee_rate,
            exchange_fee_rate=payload.exchange_fee_rate,
            research_db=research_db,
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


@router.post(
    "/rolling-backtests",
    response_model=list[FactorSelectionBacktestResult],
)
def run_rolling_factor_selection_backtests(
    payload: RollingFactorSelectionBacktestRequest,
    db: DatabaseSession,
    research_db: ResearchDatabaseSession,
) -> list[FactorSelectionBacktestResult]:
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
        persisted_list = service.run_rolling_factor_selection(
            db,
            exchange=payload.exchange,
            start_date=payload.start_date,
            end_date=payload.end_date,
            training_days=payload.training_days,
            test_days=payload.test_days,
            step_days=payload.step_days,
            name=payload.name,
            strategy_name=payload.strategy_name,
            factor_specs=factor_specs,
            top_n=payload.top_n,
            signal_lag=payload.signal_lag,
            rebalance_rule=payload.rebalance_rule,
            holding_period=payload.holding_period,
            rebalance_interval=payload.rebalance_interval,
            portfolio_constraints=_to_portfolio_constraints(
                payload.portfolio_constraints
            ),
            benchmark=payload.benchmark,
            allowed_security_types=set(payload.allowed_security_types),
            coverage_ratio=payload.coverage_ratio,
            weighting_mode=payload.weighting_mode,
            minimum_score_policy=payload.minimum_score_policy,
            minimum_score=payload.minimum_score,
            invested_weight=payload.invested_weight,
            adjust_type=payload.adjust_type,
            initial_cash=payload.initial_cash,
            commission_rate=payload.commission_rate,
            slippage_bps=payload.slippage_bps,
            lot_size=payload.lot_size,
            fee_model_version=payload.fee_model_version,
            min_commission=payload.min_commission,
            sell_stamp_duty_rate=payload.sell_stamp_duty_rate,
            transfer_fee_rate=payload.transfer_fee_rate,
            exchange_fee_rate=payload.exchange_fee_rate,
            research_db=research_db,
            code_version=payload.code_version,
            data_version=payload.data_version,
        )
        db.commit()
        research_db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    results: list[FactorSelectionBacktestResult] = []
    for window_result in persisted_list:
        window = window_result.window
        persisted = window_result.persisted
        result = persisted.result
        results.append(
            FactorSelectionBacktestResult(
                run_id=persisted.run_id,
                name=f"{payload.name}:{window.index}",
                strategy_name=payload.strategy_name,
                start_date=window.test_start_date,
                end_date=window.test_end_date,
                initial_cash=result.initial_cash,
                final_equity=result.final_equity,
                total_return=result.total_return,
                trade_count=len(result.trades),
                equity_points=len(result.equity_curve),
                position_snapshots=len(result.positions),
            )
        )
    return results
