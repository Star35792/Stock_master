from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.backtest import BacktestResult, DailyEventBacktestEngine, TargetWeightSignal
from app.repositories.market import CorporateActionRepository, DailyBarRepository
from app.repositories.strategy import BacktestPerformanceRepository, BacktestRepository
from app.schemas.common import Page
from app.schemas.strategy import (
    BacktestBenchmarkRead,
    BacktestComparisonItem,
    BacktestDriftRead,
    BacktestEquityRead,
    BacktestPerformanceRead,
    BacktestPositionRead,
    BacktestRunRead,
    BacktestTradeRead,
)
from app.services.performance import BacktestPerformanceService

_EXECUTION_ADJUST_TYPE = "none"
_CORPORATE_ACTION_POLICY = "reject"


@dataclass(frozen=True)
class PersistedBacktestResult:
    run_id: int
    result: BacktestResult


class DailyBacktestService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
        corporate_action_repository: CorporateActionRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.corporate_action_repository = (
            corporate_action_repository or CorporateActionRepository()
        )

    def run(
        self,
        db: Session,
        *,
        name: str,
        strategy_name: str,
        signals: list[TargetWeightSignal],
        start_date: date,
        end_date: date,
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        code_version: str,
        data_version: str,
        research_contract: dict[str, Any] | None = None,
    ) -> PersistedBacktestResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")
        if not signals:
            raise ValueError("signals must not be empty")
        if any(
            signal.signal_date < start_date or signal.signal_date > end_date
            for signal in signals
        ):
            raise ValueError("signal_date must be within the backtest date range")

        normalized_signals = [
            TargetWeightSignal(
                signal_date=signal.signal_date,
                symbol=signal.symbol.upper(),
                target_weight=signal.target_weight,
            )
            for signal in signals
        ]
        symbols = sorted({signal.symbol for signal in normalized_signals})
        serialized_signals = [
            {
                "signal_date": signal.signal_date.isoformat(),
                "symbol": signal.symbol,
                "target_weight": str(signal.target_weight),
            }
            for signal in sorted(
                normalized_signals,
                key=lambda signal: (
                    signal.signal_date,
                    signal.symbol,
                    str(signal.target_weight),
                ),
            )
        ]
        parameters = {
            "adjust_type": adjust_type,
            "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
            "corporate_action_policy": _CORPORATE_ACTION_POLICY,
            "commission_rate": str(commission_rate),
            "slippage_bps": str(slippage_bps),
            "lot_size": lot_size,
            "symbols": symbols,
            "signals": serialized_signals,
        }
        if research_contract is not None:
            parameters = {
                **research_contract,
                "adjust_type": adjust_type,
                "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
                "corporate_action_policy": _CORPORATE_ACTION_POLICY,
                "symbols": symbols,
                "signals": serialized_signals,
            }
        run = self.backtest_repository.create_run(
            db,
            name=name,
            strategy_name=strategy_name,
            parameters=parameters,
            code_version=code_version,
            data_version=data_version,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
        )
        try:
            corporate_actions = (
                self.corporate_action_repository.list_for_symbols_date_range(
                    db,
                    symbols=set(symbols),
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            if corporate_actions:
                raise ValueError(
                    "backtest interval contains corporate actions; "
                    "event accounting is not implemented"
                )
            execution_bars = self.daily_bar_repository.list_for_factor_calculation(
                db,
                start_date=start_date,
                end_date=end_date,
                adjust_type=_EXECUTION_ADJUST_TYPE,
                symbols=symbols,
                quality_status="trusted",
            )
            result = DailyEventBacktestEngine(
                initial_cash=initial_cash,
                commission_rate=commission_rate,
                slippage_bps=slippage_bps,
                lot_size=lot_size,
            ).run(bars=execution_bars, signals=normalized_signals)
            self.backtest_repository.save_result(db, run=run, result=result)
        except Exception as exc:
            self.backtest_repository.fail_run(db, run=run, error_message=str(exc))
            db.commit()
            raise
        return PersistedBacktestResult(run_id=run.id, result=result)


class BacktestQueryService:
    def __init__(
        self,
        backtest_repository: BacktestRepository | None = None,
        performance_repository: BacktestPerformanceRepository | None = None,
        performance_service: BacktestPerformanceService | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
    ) -> None:
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.performance_repository = (
            performance_repository or BacktestPerformanceRepository()
        )
        self.performance_service = performance_service or BacktestPerformanceService(
            performance_repository=self.performance_repository
        )
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()

    def get_benchmark_comparison(
        self, db: Session, *, run_id: int
    ) -> BacktestBenchmarkRead:
        run = self.backtest_repository.get_run(db, run_id=run_id)
        if run is None:
            raise ValueError("backtest run not found")
        if run.status != "success":
            raise ValueError("backtest run must be successful")
        benchmark = run.parameters.get("benchmark")
        if not benchmark:
            raise ValueError("benchmark is not configured for this backtest")
        bars = self.daily_bar_repository.list_by_symbol(
            db,
            symbol=benchmark.upper(),
            adjust_type=run.parameters.get("adjust_type", "qfq"),
            start_date=run.start_date,
            end_date=run.end_date,
            quality_status="trusted",
            limit=5000,
            offset=0,
        )[0]
        if len(bars) < 2:
            raise ValueError("trusted benchmark data is insufficient")
        first_close = bars[0].close
        last_close = bars[-1].close
        benchmark_return = last_close / first_close - Decimal(1)
        strategy_return = run.total_return
        periods = len(bars) - 1
        strategy_annualized = self._annualized_return(strategy_return, periods)
        benchmark_annualized = self._annualized_return(benchmark_return, periods)
        return BacktestBenchmarkRead(
            run_id=run.id,
            benchmark=benchmark.upper(),
            strategy_total_return=strategy_return,
            benchmark_total_return=benchmark_return,
            excess_return=strategy_return - benchmark_return,
            strategy_annualized_return=strategy_annualized,
            benchmark_annualized_return=benchmark_annualized,
            excess_annualized_return=strategy_annualized - benchmark_annualized,
            start_date=bars[0].trade_date,
            end_date=bars[-1].trade_date,
        )

    @staticmethod
    def _annualized_return(total_return: Decimal, periods: int) -> Decimal:
        if periods <= 0 or total_return <= Decimal(-1):
            return Decimal()
        return Decimal(str(float(Decimal(1) + total_return) ** (252 / periods) - 1))

    def compare_runs(
        self, db: Session, *, run_ids: list[int]
    ) -> list[BacktestComparisonItem]:
        if len(run_ids) < 2:
            raise ValueError("at least two run_ids are required")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_ids must be unique")
        items = []
        for run_id in run_ids:
            run = self.backtest_repository.get_run(db, run_id=run_id)
            if run is None:
                raise ValueError(f"backtest run not found: {run_id}")
            if run.status != "success":
                raise ValueError(f"backtest run must be successful: {run_id}")
            performance = self.performance_repository.get_by_run_id(db, run_id=run_id)
            if performance is None:
                raise ValueError(f"backtest performance not found: {run_id}")
            items.append(
                BacktestComparisonItem(
                    run_id=run.id,
                    name=run.name,
                    strategy_name=run.strategy_name,
                    start_date=run.start_date,
                    end_date=run.end_date,
                    total_return=performance.total_return,
                    annualized_return=performance.annualized_return,
                    maximum_drawdown=performance.maximum_drawdown,
                    sharpe_ratio=performance.sharpe_ratio,
                    turnover_rate=performance.turnover_rate,
                )
            )
        return sorted(items, key=lambda item: (-item.total_return, item.run_id))

    def detect_drift(
        self,
        db: Session,
        *,
        baseline_run_id: int,
        candidate_run_ids: list[int],
        return_tolerance: Decimal,
        drawdown_tolerance: Decimal,
    ) -> BacktestDriftRead:
        if baseline_run_id in candidate_run_ids:
            raise ValueError("baseline_run_id must not appear in candidate_run_ids")
        if len(set(candidate_run_ids)) != len(candidate_run_ids):
            raise ValueError("candidate_run_ids must be unique")
        run_ids = [baseline_run_id, *candidate_run_ids]
        runs = []
        for run_id in run_ids:
            run = self.backtest_repository.get_run(db, run_id=run_id)
            if run is None:
                raise ValueError(f"backtest run not found: {run_id}")
            if run.status != "success":
                raise ValueError(f"backtest run must be successful: {run_id}")
            runs.append(run)
        baseline = runs[0]
        baseline_parameters = self._drift_comparison_parameters(baseline.parameters)
        for candidate in runs[1:]:
            comparable = (
                candidate.strategy_name == baseline.strategy_name
                and candidate.start_date == baseline.start_date
                and candidate.end_date == baseline.end_date
                and candidate.data_version == baseline.data_version
                and self._drift_comparison_parameters(candidate.parameters)
                == baseline_parameters
            )
            if not comparable:
                raise ValueError(
                    f"backtest run is not comparable with baseline {baseline_run_id}: "
                    f"{candidate.id}"
                )
        items_by_run_id = {
            item.run_id: item for item in self.compare_runs(db, run_ids=run_ids)
        }
        baseline_item = items_by_run_id[baseline_run_id]
        return_spread = max(
            abs(items_by_run_id[run_id].total_return - baseline_item.total_return)
            for run_id in candidate_run_ids
        )
        drawdown_spread = max(
            abs(
                items_by_run_id[run_id].maximum_drawdown
                - baseline_item.maximum_drawdown
            )
            for run_id in candidate_run_ids
        )
        return BacktestDriftRead(
            baseline_run_id=baseline_run_id,
            candidate_run_ids=candidate_run_ids,
            return_tolerance=return_tolerance,
            drawdown_tolerance=drawdown_tolerance,
            return_spread=return_spread,
            drawdown_spread=drawdown_spread,
            drift_detected=(
                return_spread > return_tolerance
                or drawdown_spread > drawdown_tolerance
            ),
        )

    @staticmethod
    def _drift_comparison_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        comparable_parameters = dict(parameters)
        comparable_parameters.pop("code_version", None)
        return comparable_parameters

    def list_runs(
        self,
        db: Session,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestRunRead]:
        rows, total = self.backtest_repository.list_runs(
            db,
            strategy_name=strategy_name,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestRunRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_run(self, db: Session, *, run_id: int) -> BacktestRunRead | None:
        run = self.backtest_repository.get_run(db, run_id=run_id)
        return None if run is None else BacktestRunRead.model_validate(run)

    def list_trades(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestTradeRead]:
        rows, total = self.backtest_repository.list_trades(
            db,
            run_id=run_id,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestTradeRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_equity(
        self,
        db: Session,
        *,
        run_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestEquityRead]:
        rows, total = self.backtest_repository.list_equity(
            db,
            run_id=run_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestEquityRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_positions(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestPositionRead]:
        rows, total = self.backtest_repository.list_positions(
            db,
            run_id=run_id,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestPositionRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_performance(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> BacktestPerformanceRead | None:
        row = self.performance_repository.get_by_run_id(db, run_id=run_id)
        return None if row is None else BacktestPerformanceRead.model_validate(row)

    def analyze_performance(
        self,
        db: Session,
        *,
        run_id: int,
        code_version: str,
        style_factors: dict[str, tuple[str, str]] | None = None,
    ) -> BacktestPerformanceRead:
        self.performance_service.analyze(
            db,
            run_id=run_id,
            code_version=code_version,
            style_factors=style_factors,
        )
        performance = self.performance_repository.get_by_run_id(db, run_id=run_id)
        if performance is None:
            raise ValueError("backtest performance not found after analysis")
        return BacktestPerformanceRead.model_validate(performance)
