import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.backtest import (
    A_SHARE_COST_V1,
    ENGINE_VERSION,
    LINEAR_COMMISSION_V1,
    BacktestResult,
    DailyEventBacktestEngine,
    TargetWeightSignal,
    build_fee_model,
)
from app.repositories.market import (
    CorporateActionRepository,
    DailyBarRepository,
    TradingCalendarRepository,
)
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
_DATA_SNAPSHOT_VERSION = "trusted_market_slice_v1"
_DEFAULT_FEE_MODEL_VERSION = LINEAR_COMMISSION_V1
_A_SHARE_DEFAULT_MIN_COMMISSION = Decimal("5")
_A_SHARE_DEFAULT_STAMP_DUTY_RATE = Decimal("0.0005")
_A_SHARE_DEFAULT_TRANSFER_FEE_RATE = Decimal("0.00001")
_A_SHARE_DEFAULT_EXCHANGE_FEE_RATE = Decimal("0.0000487")
_BAR_SNAPSHOT_FIELDS = (
    "symbol",
    "trade_date",
    "security_type",
    "adjust_type",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "turnover_rate",
    "limit_up",
    "limit_down",
    "is_suspended",
    "special_treatment_status",
    "source_name",
    "source_count",
    "quality_score",
    "quality_status",
    "is_suspect",
    "selected_raw_id",
)
_ACTION_SNAPSHOT_FIELDS = (
    "symbol",
    "ex_date",
    "action_type",
    "cash_dividend",
    "bonus_share_ratio",
    "transfer_share_ratio",
    "rights_issue_ratio",
    "rights_issue_price",
    "split_ratio",
    "record_date",
    "payment_date",
    "source_name",
    "source_event_id",
)


def _fee_model_limitations(fee_model_version: str) -> list[str]:
    if fee_model_version == A_SHARE_COST_V1:
        return [
            "realistic_a_share_costs",
            "no_margin_trading_costs",
            "no_block_trading_costs",
        ]
    return [
        "no_minimum_commission",
        "no_sell_stamp_duty",
        "no_exchange_fees",
    ]


def _build_fee_contract(
    *,
    fee_model_version: str,
    commission_rate: Decimal,
    slippage_bps: Decimal,
    min_commission: Decimal | None,
    sell_stamp_duty_rate: Decimal | None,
    transfer_fee_rate: Decimal | None,
    exchange_fee_rate: Decimal | None,
) -> dict[str, str]:
    if fee_model_version == A_SHARE_COST_V1:
        return {
            "fee_model_version": A_SHARE_COST_V1,
            "fee_model_name": "a_share_cost",
            "commission_rate": str(commission_rate),
            "min_commission": str(
                min_commission if min_commission is not None else _A_SHARE_DEFAULT_MIN_COMMISSION
            ),
            "sell_stamp_duty_rate": str(
                sell_stamp_duty_rate
                if sell_stamp_duty_rate is not None
                else _A_SHARE_DEFAULT_STAMP_DUTY_RATE
            ),
            "transfer_fee_rate": str(
                transfer_fee_rate
                if transfer_fee_rate is not None
                else _A_SHARE_DEFAULT_TRANSFER_FEE_RATE
            ),
            "exchange_fee_rate": str(
                exchange_fee_rate
                if exchange_fee_rate is not None
                else _A_SHARE_DEFAULT_EXCHANGE_FEE_RATE
            ),
            "slippage_bps": str(slippage_bps),
        }
    if fee_model_version == LINEAR_COMMISSION_V1:
        return {
            "fee_model_version": LINEAR_COMMISSION_V1,
            "fee_model_name": "linear_commission",
            "commission_rate": str(commission_rate),
            "min_commission": str(min_commission if min_commission is not None else Decimal("0")),
            "sell_stamp_duty_rate": str(
                sell_stamp_duty_rate if sell_stamp_duty_rate is not None else Decimal("0")
            ),
            "transfer_fee_rate": str(
                transfer_fee_rate if transfer_fee_rate is not None else Decimal("0")
            ),
            "exchange_fee_rate": str(
                exchange_fee_rate if exchange_fee_rate is not None else Decimal("0")
            ),
            "slippage_bps": str(slippage_bps),
        }
    raise ValueError(f"unsupported fee_model_version: {fee_model_version}")


class BacktestDataChangedError(RuntimeError):
    """Raised when trusted market inputs change while a backtest is running."""

    code = "backtest_data_changed"


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
        trading_calendar_repository: TradingCalendarRepository | None = None,
        verification_session_factory: Callable[[Session], Any] | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.corporate_action_repository = (
            corporate_action_repository or CorporateActionRepository()
        )
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.verification_session_factory = (
            verification_session_factory or _independent_verification_session
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
        fee_model_version: str | None = None,
        min_commission: Decimal | None = None,
        sell_stamp_duty_rate: Decimal | None = None,
        transfer_fee_rate: Decimal | None = None,
        exchange_fee_rate: Decimal | None = None,
        training_artifact_id: int | None = None,
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
        resolved_fee_model_version = fee_model_version or _DEFAULT_FEE_MODEL_VERSION
        fee_contract = _build_fee_contract(
            fee_model_version=resolved_fee_model_version,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            min_commission=min_commission,
            sell_stamp_duty_rate=sell_stamp_duty_rate,
            transfer_fee_rate=transfer_fee_rate,
            exchange_fee_rate=exchange_fee_rate,
        )
        fee_model = build_fee_model(
            fee_model_version=resolved_fee_model_version,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            min_commission=Decimal(fee_contract["min_commission"]),
            sell_stamp_duty_rate=Decimal(fee_contract["sell_stamp_duty_rate"]),
            transfer_fee_rate=Decimal(fee_contract["transfer_fee_rate"]),
            exchange_fee_rate=Decimal(fee_contract["exchange_fee_rate"]),
        )
        parameters = {
            "adjust_type": adjust_type,
            "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
            "corporate_action_policy": _CORPORATE_ACTION_POLICY,
            "engine_version": ENGINE_VERSION,
            **fee_contract,
            "calculation_precision_policy": "unquantized_decimal_until_persistence_v1",
            "fee_model_limitations": _fee_model_limitations(resolved_fee_model_version),
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
                "engine_version": ENGINE_VERSION,
                **fee_contract,
                "calculation_precision_policy": (
                    "unquantized_decimal_until_persistence_v1"
                ),
                "fee_model_limitations": _fee_model_limitations(
                    resolved_fee_model_version
                ),
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
            training_artifact_id=training_artifact_id,
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
            execution_calendar = self._load_execution_calendar(
                db,
                symbols=symbols,
                start_date=start_date,
                end_date=end_date,
            )
            snapshot_before = _build_market_snapshot(
                bars=execution_bars,
                corporate_actions=corporate_actions,
                execution_calendar=execution_calendar,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
            data_snapshot = {
                **snapshot_before,
                "verification_status": "pending",
            }
            self.backtest_repository.update_data_snapshot(
                db,
                run=run,
                data_snapshot=data_snapshot,
            )
            result = DailyEventBacktestEngine(
                initial_cash=initial_cash,
                lot_size=lot_size,
                fee_model=fee_model,
            ).run(
                bars=execution_bars,
                signals=normalized_signals,
                execution_calendar=execution_calendar,
            )
            with self.verification_session_factory(db) as verification_db:
                verification_scope = (
                    "independent_session"
                    if verification_db is not db
                    else "same_session_fallback"
                )
                corporate_actions_after = (
                    self.corporate_action_repository.list_for_symbols_date_range(
                        verification_db,
                        symbols=set(symbols),
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
                execution_bars_after = (
                    self.daily_bar_repository.list_for_factor_calculation(
                        verification_db,
                        start_date=start_date,
                        end_date=end_date,
                        adjust_type=_EXECUTION_ADJUST_TYPE,
                        symbols=symbols,
                        quality_status="trusted",
                    )
                )
                execution_calendar_after = self._load_execution_calendar(
                    verification_db,
                    symbols=set(symbols),
                    start_date=start_date,
                    end_date=end_date,
                )
            snapshot_after = _build_market_snapshot(
                bars=execution_bars_after,
                corporate_actions=corporate_actions_after,
                execution_calendar=execution_calendar_after,
                start_date=start_date,
                end_date=end_date,
                symbols=symbols,
            )
            verification_status = (
                "verified"
                if snapshot_after["sha256"] == snapshot_before["sha256"]
                else "changed"
            )
            data_snapshot = {
                **snapshot_before,
                "verification_status": verification_status,
                "verification_scope": verification_scope,
                "post_run_sha256": snapshot_after["sha256"],
            }
            self.backtest_repository.update_data_snapshot(
                db,
                run=run,
                data_snapshot=data_snapshot,
            )
            if verification_status == "changed":
                raise BacktestDataChangedError(
                    "backtest_data_changed: trusted market data changed while "
                    "the backtest was running"
                )
            self.backtest_repository.save_result(db, run=run, result=result)
        except Exception as exc:
            self.backtest_repository.fail_run(db, run=run, error_message=str(exc))
            db.commit()
            raise
        return PersistedBacktestResult(run_id=run.id, result=result)

    def _load_execution_calendar(
        self,
        db: Session,
        *,
        symbols: list[str] | set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, list[date]]:
        symbols_by_exchange: dict[str, list[str]] = {}
        for symbol in sorted(symbols):
            exchange = _symbol_exchange(symbol)
            if exchange is None:
                raise ValueError(
                    "cannot resolve exchange from symbol for execution calendar: "
                    f"{symbol}"
                )
            symbols_by_exchange.setdefault(exchange, []).append(symbol)
        result: dict[str, list[date]] = {}
        for exchange, exchange_symbols in symbols_by_exchange.items():
            dates = self.trading_calendar_repository.list_open_dates(
                db,
                exchange=exchange,
                start_date=start_date,
                end_date=end_date,
            )
            if not dates:
                raise ValueError(
                    "trusted trading calendar is unavailable for exchange "
                    f"{exchange} in the backtest interval"
                )
            for symbol in exchange_symbols:
                result[symbol] = dates
        return result


def _build_market_snapshot(
    *,
    bars: list[Any],
    corporate_actions: list[Any],
    execution_calendar: dict[str, list[date]],
    start_date: date,
    end_date: date,
    symbols: list[str],
) -> dict[str, Any]:
    bar_records = [_snapshot_record(row, _BAR_SNAPSHOT_FIELDS) for row in bars]
    action_records = [
        _snapshot_record(row, _ACTION_SNAPSHOT_FIELDS) for row in corporate_actions
    ]
    bar_records.sort(key=_snapshot_sort_key)
    action_records.sort(key=_snapshot_sort_key)
    calendar_records = [
        {
            "symbol": symbol,
            "trade_dates": [trade_date.isoformat() for trade_date in dates],
        }
        for symbol, dates in sorted(execution_calendar.items())
    ]
    content = json.dumps(
        {
            "bars": bar_records,
            "corporate_actions": action_records,
            "execution_calendar": calendar_records,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "snapshot_version": _DATA_SNAPSHOT_VERSION,
        "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
        "quality_status": "trusted",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbols": list(symbols),
        "bar_count": len(bar_records),
        "corporate_action_count": len(action_records),
        "calendar_session_count": sum(
            len(record["trade_dates"]) for record in calendar_records
        ),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _snapshot_record(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {
        field: _snapshot_value(getattr(row, field, None))
        for field in fields
    }


def _snapshot_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _snapshot_sort_key(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _symbol_exchange(symbol: str) -> str | None:
    if "." not in symbol:
        return None
    exchange = symbol.rsplit(".", 1)[1].upper()
    return exchange if exchange in {"SH", "SZ", "BJ", "HK"} else None


@contextmanager
def _independent_verification_session(db: Session) -> Iterator[Session]:
    get_bind = getattr(db, "get_bind", None)
    if not callable(get_bind):
        yield db
        return
    with Session(bind=get_bind()) as verification_db:
        yield verification_db


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
