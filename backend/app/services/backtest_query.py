"""Backtest result queries and comparisons; execution lives in backtest.py."""

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.backtest import BacktestPerformanceAnalyzer
from app.config.settings import settings
from app.repositories.market import DailyBarRepository
from app.repositories.strategy import BacktestPerformanceRepository, BacktestRepository
from app.schemas.common import Page
from app.schemas.strategy import (
    BacktestBenchmarkRead,
    BacktestComparisonItem,
    BacktestComparisonPreviewItem,
    BacktestDriftRead,
    BacktestEquityRead,
    BacktestFinalHoldingRead,
    BacktestFinalHoldingsRead,
    BacktestPerformanceRead,
    BacktestPositionRead,
    BacktestRunListItemRead,
    BacktestRunRead,
    BacktestTradeRead,
)
from app.services.performance import BacktestPerformanceService

_PREVIEW_STRATEGY_FIELDS = (
    "factor_specs", "top_n", "signal_lag", "rebalance_rule", "rebalance_interval",
    "portfolio_constraints", "holding_period", "benchmark", "allowed_security_types",
    "symbols", "coverage_ratio", "weighting_mode", "minimum_score_policy",
    "minimum_score", "invested_weight", "selection_policy", "lot_size",
    "training_start_date", "training_end_date", "validation_window_id",
    "training_artifact_verified",
)


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
        self.equity_analyzer = BacktestPerformanceAnalyzer()

    def get_benchmark_comparison(
        self,
        db: Session,
        *,
        run_id: int,
        research_db: Session | None = None,
    ) -> BacktestBenchmarkRead:
        artifacts_db = research_db if research_db is not None else db
        run = self.backtest_repository.get_run(artifacts_db, run_id=run_id)
        if run is None:
            raise ValueError("backtest run not found")
        if run.status != "success":
            raise ValueError("backtest run must be successful")
        benchmark = run.parameters.get("benchmark")
        if not benchmark:
            raise ValueError("benchmark is not configured for this backtest")
        equities, equity_total = self.backtest_repository.list_equity(
            artifacts_db, run_id=run_id, limit=5000, offset=0
        )
        if equity_total < 2 or equity_total > len(equities):
            raise ValueError("strategy equity dates are insufficient for benchmark comparison")
        equity_dates = [row.trade_date for row in equities]
        if equity_dates != sorted(set(equity_dates)):
            raise ValueError("strategy equity dates are invalid for benchmark comparison")
        snapshot = run.parameters.get("benchmark_snapshot")
        if snapshot is not None:
            if snapshot.get("status") != "verified":
                raise ValueError(snapshot.get("reason", "benchmark snapshot unavailable"))
            if (
                snapshot.get("start_date") != equity_dates[0].isoformat()
                or snapshot.get("end_date") != equity_dates[-1].isoformat()
                or snapshot.get("date_count") != len(equity_dates)
            ):
                raise ValueError("benchmark snapshot does not match strategy equity dates")
            first_close = Decimal(snapshot["first_close"])
            last_close = Decimal(snapshot["last_close"])
            input_source = "run_snapshot"
        else:
            bars = self.daily_bar_repository.list_by_symbol(
                db,
                symbol=benchmark.upper(),
                adjust_type=run.parameters.get("adjust_type", "qfq"),
                start_date=equity_dates[0],
                end_date=equity_dates[-1],
                quality_status="trusted",
                limit=5000,
                offset=0,
            )[0]
            if [bar.trade_date for bar in bars] != equity_dates:
                raise ValueError("trusted benchmark dates do not cover strategy equity dates")
            first_close = bars[0].close
            last_close = bars[-1].close
            input_source = "current_market_unversioned"
        if first_close <= 0 or equities[0].total_equity <= 0:
            raise ValueError("benchmark comparison requires positive starting values")
        benchmark_return = last_close / first_close - Decimal(1)
        strategy_return = equities[-1].total_equity / equities[0].total_equity - Decimal(1)
        periods = len(equity_dates) - 1
        strategy_annualized = self._annualized_return(strategy_return, periods)
        benchmark_annualized = self._annualized_return(benchmark_return, periods)
        return BacktestBenchmarkRead(
            run_id=run.id,
            benchmark=benchmark.upper(),
            input_source=input_source,
            strategy_total_return=strategy_return,
            benchmark_total_return=benchmark_return,
            excess_return=strategy_return - benchmark_return,
            strategy_annualized_return=strategy_annualized,
            benchmark_annualized_return=benchmark_annualized,
            excess_annualized_return=strategy_annualized - benchmark_annualized,
            start_date=equity_dates[0],
            end_date=equity_dates[-1],
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

    def compare_preview(
        self, db: Session, *, run_ids: list[int]
    ) -> list[BacktestComparisonPreviewItem]:
        if not 2 <= len(run_ids) <= 20:
            raise ValueError("between two and twenty run_ids are required")
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("run_ids must be unique")
        items = []
        fee_fields = (
            "commission_rate", "min_commission", "sell_stamp_duty_rate",
            "transfer_fee_rate", "exchange_fee_rate", "slippage_bps",
            "security_type_fee_rule", "stock_stamp_duty_rule", "supported_trade_date_start",
        )
        for run_id in run_ids:
            run = self.backtest_repository.get_run(db, run_id=run_id)
            if run is None:
                raise ValueError(f"backtest run not found: {run_id}")
            if run.status != "success":
                raise ValueError(f"backtest run must be successful: {run_id}")
            performance = self.performance_repository.get_by_run_id(db, run_id=run_id)
            source = "persisted"
            code_version = performance.code_version if performance else settings.app_code_version
            if performance is None:
                _, equity_rows, trade_rows, position_rows = (
                    self.performance_repository.load_inputs(db, run_id=run_id)
                )
                if not equity_rows:
                    source = "missing_equity"
                    code_version = None
                else:
                    try:
                        performance = self.equity_analyzer.analyze(
                            run=run,
                            equity_rows=equity_rows,
                            trade_rows=trade_rows,
                            position_rows=position_rows,
                            industries={},
                        )
                        source = "derived"
                    except ValueError:
                        source = "invalid_equity"
                        code_version = None
            parameters = run.parameters or {}
            strategy_contract = {
                "strategy_name": run.strategy_name,
                "training_artifact_id": getattr(run, "training_artifact_id", None),
                **{
                    field: parameters[field]
                    for field in _PREVIEW_STRATEGY_FIELDS if field in parameters
                },
            }
            for snapshot_field, target in (
                ("strategy_input_snapshot", "strategy_input_sha256"),
                ("data_snapshot", "data_snapshot_sha256"),
            ):
                snapshot = parameters.get(snapshot_field)
                if isinstance(snapshot, dict):
                    strategy_contract[target] = snapshot.get("sha256")
            items.append(BacktestComparisonPreviewItem(
                run_id=run.id,
                name=run.name,
                strategy_name=run.strategy_name,
                start_date=run.start_date,
                end_date=run.end_date,
                data_version=run.data_version,
                run_code_version=run.code_version,
                initial_cash=run.initial_cash,
                validation_mode=parameters.get("validation_mode"),
                fee_model_version=parameters.get("fee_model_version"),
                fee_parameters={field: parameters.get(field) for field in fee_fields},
                adjust_type=parameters.get("adjust_type"),
                strategy_contract=strategy_contract,
                strategy_differences=[],
                metric_source=source,
                metric_code_version=code_version,
                total_return=performance.total_return if performance else None,
                annualized_return=performance.annualized_return if performance else None,
                maximum_drawdown=performance.maximum_drawdown if performance else None,
                sharpe_ratio=performance.sharpe_ratio if performance else None,
                turnover_rate=performance.turnover_rate if performance else None,
                total_commission=performance.total_commission if performance else None,
                delta_status="baseline" if not items else "unavailable",
                delta_reasons=[],
            ))
        dates_by_id = self.backtest_repository.list_equity_dates_by_run_ids(
            db, run_ids=run_ids
        )
        baseline = items[0]
        for item in items[1:]:
            item.strategy_differences = [
                field for field in dict.fromkeys(
                    (*baseline.strategy_contract, *item.strategy_contract)
                )
                if self._strategy_contract_value_differs(
                    field,
                    baseline.strategy_contract.get(field),
                    item.strategy_contract.get(field),
                )
            ]
            reasons = self._preview_delta_reasons(
                baseline, item, dates_by_id=dates_by_id
            )
            if reasons:
                item.delta_reasons = reasons
                continue
            item.delta_status = "available"
            item.total_return_delta = item.total_return - baseline.total_return
            item.maximum_drawdown_delta = (
                item.maximum_drawdown - baseline.maximum_drawdown
            )
            if item.sharpe_ratio is not None and baseline.sharpe_ratio is not None:
                item.sharpe_ratio_delta = item.sharpe_ratio - baseline.sharpe_ratio
        return items

    @staticmethod
    def _strategy_contract_value_differs(
        field: str, baseline: Any, candidate: Any
    ) -> bool:
        if field == "symbols" and isinstance(baseline, list) and isinstance(candidate, list):
            if all(isinstance(symbol, str) for symbol in (*baseline, *candidate)):
                return set(baseline) != set(candidate)
        return baseline != candidate

    @staticmethod
    def _preview_delta_reasons(
        baseline: BacktestComparisonPreviewItem,
        candidate: BacktestComparisonPreviewItem,
        *,
        dates_by_id: dict[int, tuple[date, ...]],
    ) -> list[str]:
        reasons = []
        if baseline.total_return is None or baseline.maximum_drawdown is None:
            reasons.append("baseline_metrics_unavailable")
        if candidate.total_return is None or candidate.maximum_drawdown is None:
            reasons.append("candidate_metrics_unavailable")
        compared_fields = (
            "start_date", "end_date", "data_version", "validation_mode",
            "run_code_version", "adjust_type", "fee_model_version",
            "fee_parameters", "initial_cash",
        )
        reasons.extend(
            field for field in compared_fields
            if getattr(baseline, field) != getattr(candidate, field)
        )
        if baseline.validation_mode is None or candidate.validation_mode is None:
            reasons.append("validation_mode_missing")
        if baseline.adjust_type is None or candidate.adjust_type is None:
            reasons.append("adjust_type_missing")
        required_fee_fields = (
            "commission_rate", "min_commission", "sell_stamp_duty_rate",
            "transfer_fee_rate", "exchange_fee_rate", "slippage_bps",
        )
        if baseline.fee_model_version is None or candidate.fee_model_version is None or any(
            baseline.fee_parameters[field] is None or candidate.fee_parameters[field] is None
            for field in required_fee_fields
        ):
            reasons.append("fee_contract_missing")
        if (
            baseline.metric_code_version is None
            or candidate.metric_code_version is None
            or baseline.metric_code_version != candidate.metric_code_version
        ):
            reasons.append("metric_code_version")
        baseline_dates = dates_by_id.get(baseline.run_id)
        candidate_dates = dates_by_id.get(candidate.run_id)
        if not baseline_dates or not candidate_dates:
            reasons.append("equity_dates_missing")
        elif baseline_dates != candidate_dates:
            reasons.append("equity_dates_differ")
        return reasons

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
        validation_mode: str | None = None,
        data_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestRunListItemRead]:
        rows, total = self.backtest_repository.list_runs(
            db,
            strategy_name=strategy_name,
            status=status,
            validation_mode=validation_mode,
            data_version=data_version,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(items=self._run_items(db, rows), total=total, limit=limit, offset=offset)

    def _run_items(self, db: Session, rows: list[Any]) -> list[BacktestRunListItemRead]:
        sharpe_by_id = self.performance_repository.list_sharpe_by_run_ids(
            db, run_ids=[row.id for row in rows if row.status == "success"]
        )
        missing_ids = [
            row.id for row in rows if row.status == "success" and row.id not in sharpe_by_id
        ]
        equity_by_id = self.backtest_repository.list_equity_values_by_run_ids(
            db, run_ids=missing_ids
        )
        items = []
        for row in rows:
            persisted = sharpe_by_id.get(row.id)
            sharpe = persisted[0] if persisted else None
            version = persisted[1] if persisted else None
            if row.status != "success":
                performance_status = "not_applicable"
            elif persisted is not None:
                performance_status = "analyzed"
            elif not equity_by_id.get(row.id):
                performance_status = "missing_equity"
            else:
                try:
                    metrics = self.equity_analyzer.analyze_equity(
                        initial_cash=row.initial_cash, equities=equity_by_id[row.id]
                    )
                    sharpe = metrics.sharpe_ratio
                    version = settings.app_code_version
                    performance_status = "derived"
                except ValueError:
                    performance_status = "invalid_equity"
            items.append(
                BacktestRunListItemRead.model_validate(
                    {
                        **BacktestRunRead.model_validate(row).model_dump(),
                        "sharpe_ratio": sharpe,
                        "performance_status": performance_status,
                        "performance_code_version": version,
                    }
                )
            )
        return items

    def get_run_summary(self, db: Session, *, run_id: int) -> BacktestRunListItemRead | None:
        run = self.backtest_repository.get_run(db, run_id=run_id)
        return None if run is None else self._run_items(db, [run])[0]

    def get_run(self, db: Session, *, run_id: int) -> BacktestRunRead | None:
        run = self.backtest_repository.get_run(db, run_id=run_id)
        return None if run is None else BacktestRunRead.model_validate(run)

    def get_final_holdings(
        self,
        db: Session,
        *,
        run_id: int,
        research_db: Session | None = None,
        security_repository: Any | None = None,
    ) -> BacktestFinalHoldingsRead | None:
        """Final-day book of a finished run, as a plan-prefill source.

        Artifacts come from the research database; industry/type/name lookups
        stay on the main database's ``market.security`` (current labels, same
        approximation as the backtest constraint path).
        """
        artifacts_db = research_db if research_db is not None else db
        run = self.backtest_repository.get_run(artifacts_db, run_id=run_id)
        if run is None:
            return None
        if run.status != "success":
            raise ValueError("backtest run must be successful")
        positions = self.backtest_repository.latest_positions(artifacts_db, run_id=run_id)
        equity = self.backtest_repository.latest_equity(artifacts_db, run_id=run_id)
        as_of = equity.trade_date if equity is not None else run.end_date
        total_equity = (
            equity.total_equity if equity is not None else run.final_equity or Decimal()
        )
        cash = equity.cash if equity is not None else Decimal()
        from app.repositories.market import SecurityRepository

        repository = security_repository or SecurityRepository()
        profiles = {
            security.symbol.upper(): security
            for security in repository.get_many(
                db, symbols={row.symbol for row in positions}
            )
        }
        holdings: list[BacktestFinalHoldingRead] = []
        for row in positions:
            security = profiles.get(row.symbol.upper())
            weight = (
                row.market_value / total_equity if total_equity > 0 else Decimal()
            )
            holdings.append(
                BacktestFinalHoldingRead(
                    symbol=row.symbol,
                    name=security.name if security else None,
                    industry=security.industry if security else None,
                    security_type=security.security_type if security else None,
                    quantity=row.quantity,
                    close=row.close,
                    market_value=row.market_value,
                    weight=weight.quantize(Decimal("0.0000000001")),
                )
            )
        return BacktestFinalHoldingsRead(
            run_id=run_id,
            as_of_date=as_of,
            cash=cash,
            total_equity=total_equity,
            holdings=holdings,
        )

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
        research_db: Session | None = None,
    ) -> BacktestPerformanceRead:
        self.performance_service.analyze(
            db,
            research_db=research_db,
            run_id=run_id,
            code_version=code_version,
            style_factors=style_factors,
        )
        artifacts_db = research_db if research_db is not None else db
        performance = self.performance_repository.get_by_run_id(artifacts_db, run_id=run_id)
        if performance is None:
            raise ValueError("backtest performance not found after analysis")
        return BacktestPerformanceRead.model_validate(performance)
