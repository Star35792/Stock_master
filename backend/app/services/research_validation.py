import hashlib
import json
from datetime import date
from decimal import Decimal, localcontext
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.strategy import BacktestPerformanceRepository, BacktestRepository
from app.schemas.research_validation import (
    ResearchValidationAggregateRead,
    ResearchValidationReportRead,
    ResearchValidationWindowRead,
)
from app.services.backtest import BacktestQueryService

_COMPARABLE_PARAMETER_KEYS = (
    "strategy_name",
    "factor_specs",
    "top_n",
    "signal_lag",
    "rebalance_rule",
    "holding_period",
    "benchmark",
    "allowed_security_types",
    "adjust_type",
    "execution_adjust_type",
    "corporate_action_policy",
    "initial_cash",
    "commission_rate",
    "fee_model_version",
    "min_commission",
    "minimum_commission",
    "sell_stamp_duty_rate",
    "transfer_fee_rate",
    "exchange_fee_rate",
    "slippage_bps",
    "lot_size",
    "coverage_ratio",
    "weighting_mode",
    "minimum_score_policy",
    "minimum_score",
    "invested_weight",
)


class ResearchValidationReportService:
    def __init__(
        self,
        *,
        backtest_repository: BacktestRepository | None = None,
        performance_repository: BacktestPerformanceRepository | None = None,
        backtest_query_service: BacktestQueryService | None = None,
    ) -> None:
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.performance_repository = (
            performance_repository or BacktestPerformanceRepository()
        )
        self.backtest_query_service = backtest_query_service or BacktestQueryService(
            backtest_repository=self.backtest_repository,
            performance_repository=self.performance_repository,
        )

    def build_report(
        self,
        db: Session,
        *,
        run_ids: list[int],
    ) -> ResearchValidationReportRead:
        if not run_ids:
            raise ValueError("run_ids must not be empty")
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("run_ids must be unique")
        runs = []
        for run_id in run_ids:
            run = self.backtest_repository.get_run(db, run_id=run_id)
            if run is None:
                raise ValueError(f"backtest run not found: {run_id}")
            runs.append(run)

        validation_modes = {run.parameters.get("validation_mode") for run in runs}
        if len(validation_modes) != 1 or validation_modes.pop() not in {
            "out_of_sample",
            "rolling",
        }:
            raise ValueError("runs must share one validation mode: out_of_sample or rolling")
        validation_mode = runs[0].parameters["validation_mode"]
        if validation_mode == "rolling" and len(runs) < 2:
            raise ValueError("rolling validation reports require at least two runs")

        comparison_contract = self._comparison_contract(runs[0])
        conflicts: set[str] = set()
        for run in runs[1:]:
            candidate_contract = self._comparison_contract(run)
            conflicts.update(
                key
                for key in comparison_contract
                if candidate_contract.get(key) != comparison_contract.get(key)
            )
        if conflicts:
            raise ValueError(
                "validation runs have incompatible contract fields: "
                + ",".join(sorted(conflicts))
            )

        bounds = [self._validation_bounds(run) for run in runs]
        if validation_mode == "rolling":
            test_starts = [item[2] for item in bounds]
            if test_starts != sorted(test_starts) or len(set(test_starts)) != len(
                test_starts
            ):
                raise ValueError(
                    "rolling validation test start dates must be strictly increasing"
                )

        windows = [
            self._build_window(db, index=index, run=run, bounds=bounds[index])
            for index, run in enumerate(runs)
        ]
        successful = [window for window in windows if window.status == "success"]
        overlapping = any(
            current[2] <= previous[3]
            for previous, current in zip(bounds, bounds[1:], strict=False)
        )
        contract_json = json.dumps(
            comparison_contract,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return ResearchValidationReportRead(
            validation_mode=validation_mode,
            strategy_name=runs[0].strategy_name,
            run_ids=run_ids,
            window_count=len(runs),
            successful_window_count=len(successful),
            failed_window_count=len(runs) - len(successful),
            success_ratio=Decimal(len(successful)) / Decimal(len(runs)),
            contract_hash=hashlib.sha256(contract_json.encode("utf-8")).hexdigest(),
            comparison_contract=comparison_contract,
            data_version=runs[0].data_version,
            code_version=runs[0].code_version,
            benchmark=runs[0].parameters.get("benchmark"),
            overlapping_test_windows=overlapping,
            limitation=(
                "Training dates are recorded, but training, parameter selection, and "
                "artifact provenance are not verified; overlapping windows are not compounded."
            ),
            windows=windows,
            aggregate=self._aggregate(successful),
        )

    @staticmethod
    def _comparison_contract(run: Any) -> dict[str, Any]:
        contract = {
            key: run.parameters.get(key) for key in _COMPARABLE_PARAMETER_KEYS
        }
        contract.update(
            {
                "strategy_name": run.strategy_name,
                "data_version": run.data_version,
                "code_version": run.code_version,
            }
        )
        return contract

    @staticmethod
    def _validation_bounds(run: Any) -> tuple[date, date, date, date]:
        parameters = run.parameters
        training_start = parameters.get("training_start_date")
        training_end = parameters.get("training_end_date")
        test_start = parameters.get("test_start_date") or run.start_date.isoformat()
        test_end = parameters.get("test_end_date") or run.end_date.isoformat()
        if not all((training_start, training_end, test_start, test_end)):
            raise ValueError(f"validation dates are incomplete for run {run.id}")
        parsed = tuple(
            value if isinstance(value, date) else date.fromisoformat(value)
            for value in (training_start, training_end, test_start, test_end)
        )
        if parsed[0] > parsed[1] or parsed[2] > parsed[3]:
            raise ValueError(f"validation date range is invalid for run {run.id}")
        if parsed[1] >= parsed[2]:
            raise ValueError(f"training period must end before test period for run {run.id}")
        if parsed[2] != run.start_date or parsed[3] != run.end_date:
            raise ValueError(f"test window does not match run dates for run {run.id}")
        return parsed

    def _build_window(
        self,
        db: Session,
        *,
        index: int,
        run: Any,
        bounds: tuple[date, date, date, date],
    ) -> ResearchValidationWindowRead:
        performance = (
            self.performance_repository.get_by_run_id(db, run_id=run.id)
            if run.status == "success"
            else None
        )
        error_message = run.error_message
        if run.status == "success" and performance is None:
            error_message = "backtest performance not found"
        window_success = run.status == "success" and performance is not None
        trade_count = 0
        if window_success:
            _, trade_count = self.backtest_repository.list_trades(
                db,
                run_id=run.id,
                limit=1,
                offset=0,
            )

        benchmark_status = "not_configured"
        benchmark_return = None
        excess_return = None
        benchmark_error = None
        if run.parameters.get("benchmark") and not window_success:
            benchmark_status = "not_applicable"
        elif run.parameters.get("benchmark"):
            try:
                benchmark = self.backtest_query_service.get_benchmark_comparison(
                    db,
                    run_id=run.id,
                )
                benchmark_status = "available"
                benchmark_return = benchmark.benchmark_total_return
                excess_return = benchmark.excess_return
            except ValueError as exc:
                benchmark_status = "unavailable"
                benchmark_error = str(exc)

        def metric(name: str) -> Any:
            return getattr(performance, name) if performance is not None else None

        return ResearchValidationWindowRead(
            index=index,
            run_id=run.id,
            status="success" if window_success else "failed",
            run_status=run.status,
            error_message=error_message,
            training_start_date=bounds[0],
            training_end_date=bounds[1],
            test_start_date=bounds[2],
            test_end_date=bounds[3],
            total_return=metric("total_return"),
            annualized_return=metric("annualized_return"),
            maximum_drawdown=metric("maximum_drawdown"),
            annualized_volatility=metric("annualized_volatility"),
            sharpe_ratio=metric("sharpe_ratio"),
            calmar_ratio=metric("calmar_ratio"),
            daily_win_rate=metric("daily_win_rate"),
            profit_loss_ratio=metric("profit_loss_ratio"),
            turnover_rate=metric("turnover_rate"),
            total_commission=metric("total_commission"),
            trade_count=trade_count,
            benchmark_status=benchmark_status,
            benchmark_return=benchmark_return,
            excess_return=excess_return,
            benchmark_error=benchmark_error,
        )

    @classmethod
    def _aggregate(
        cls,
        windows: list[ResearchValidationWindowRead],
    ) -> ResearchValidationAggregateRead:
        returns = [window.total_return for window in windows if window.total_return is not None]
        drawdowns = [
            window.maximum_drawdown
            for window in windows
            if window.maximum_drawdown is not None
        ]
        sharpes = [window.sharpe_ratio for window in windows if window.sharpe_ratio is not None]
        turnovers = [
            window.turnover_rate for window in windows if window.turnover_rate is not None
        ]
        excess_returns = [
            window.excess_return for window in windows if window.excess_return is not None
        ]
        return ResearchValidationAggregateRead(
            return_sample_count=len(returns),
            mean_total_return=cls._mean(returns),
            median_total_return=(Decimal(str(median(returns))) if returns else None),
            minimum_total_return=min(returns) if returns else None,
            maximum_total_return=max(returns) if returns else None,
            total_return_standard_deviation=cls._standard_deviation(returns),
            positive_return_ratio=cls._positive_ratio(returns),
            worst_maximum_drawdown=min(drawdowns) if drawdowns else None,
            sharpe_sample_count=len(sharpes),
            mean_sharpe_ratio=cls._mean(sharpes),
            turnover_sample_count=len(turnovers),
            mean_turnover_rate=cls._mean(turnovers),
            total_commission=sum(
                (
                    window.total_commission
                    for window in windows
                    if window.total_commission is not None
                ),
                Decimal(),
            ),
            total_trade_count=sum(window.trade_count for window in windows),
            benchmark_sample_count=len(excess_returns),
            benchmark_coverage_ratio=(
                Decimal(len(excess_returns)) / Decimal(len(windows)) if windows else None
            ),
            mean_excess_return=cls._mean(excess_returns),
            positive_excess_return_ratio=cls._positive_ratio(excess_returns),
        )

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal | None:
        return sum(values, Decimal()) / Decimal(len(values)) if values else None

    @classmethod
    def _standard_deviation(cls, values: list[Decimal]) -> Decimal | None:
        mean = cls._mean(values)
        if mean is None:
            return None
        variance = sum(((value - mean) ** 2 for value in values), Decimal()) / Decimal(
            len(values)
        )
        with localcontext() as context:
            context.prec = 28
            return variance.sqrt()

    @staticmethod
    def _positive_ratio(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return Decimal(sum(value > 0 for value in values)) / Decimal(len(values))
