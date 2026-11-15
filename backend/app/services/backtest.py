from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.backtest import (
    A_SHARE_COST_V1,
    A_SHARE_COST_V2,
    ENGINE_VERSION,
    LINEAR_COMMISSION_V1,
    BacktestResult,
    DailyEventBacktestEngine,
    TargetWeightSignal,
    build_fee_model,
)
from app.backtest.performance import BacktestPerformanceAnalyzer
from app.backtest.quantile_result import (
    QUANTILE_DECLARATIONS,
    QuantileBacktestMeta,
    QuantileBacktestResult,
    QuantileLayerResult,
    quantile_result_to_json,
)
from app.config.settings import settings
from app.portfolio import PortfolioConstraints
from app.repositories.market import (
    CorporateActionRepository,
    DailyBarRepository,
    TradingCalendarRepository,
)
from app.repositories.research_read import WarmResearchReadRepository
from app.repositories.strategy import BacktestRepository
from app.services.backtest_query import BacktestQueryService as BacktestQueryService
from app.services.backtest_snapshot import (
    _EXECUTION_ADJUST_TYPE,
    _build_market_snapshot,
    build_benchmark_snapshot,
    build_input_data_version,
)

_CORPORATE_ACTION_POLICY = "reject"
_DEFAULT_FEE_MODEL_VERSION = LINEAR_COMMISSION_V1
_A_SHARE_DEFAULT_MIN_COMMISSION = Decimal("5")
_A_SHARE_DEFAULT_STAMP_DUTY_RATE = Decimal("0.0005")
_A_SHARE_DEFAULT_TRANSFER_FEE_RATE = Decimal("0.00001")
_A_SHARE_DEFAULT_EXCHANGE_FEE_RATE = Decimal("0.0000487")


def _fee_model_limitations(fee_model_version: str) -> list[str]:
    if fee_model_version == A_SHARE_COST_V2:
        return [
            "instrument_aware_cost_assumptions",
            "no_margin_trading_costs",
            "no_block_trading_costs",
            "no_broker_specific_fee_schedule",
        ]
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
    if fee_model_version in {A_SHARE_COST_V1, A_SHARE_COST_V2}:
        return {
            "fee_model_version": fee_model_version,
            "fee_model_name": (
                "a_share_cost_instrument_aware" if fee_model_version == A_SHARE_COST_V2
                else "a_share_cost"
            ),
            **(
                {
                    "security_type_fee_rule": "stock_or_etf_v1",
                    "stock_stamp_duty_rule": (
                        "legal_20230828_v1"
                        if sell_stamp_duty_rate is None else "fixed_rate_v1"
                    ),
                    "supported_trade_date_start": "2022-04-29",
                }
                if fee_model_version == A_SHARE_COST_V2 else {}
            ),
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
    quantile: "QuantileBacktestResult | None" = None


class DailyBacktestService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
        corporate_action_repository: CorporateActionRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        verification_session_factory: Callable[[Session], Any] | None = None,
        research_read_source: str | None = None,
        research_release_tag: str | None = None,
        research_min_trading_days: int | None = None,
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
        # ``warm`` routes research reads through WarmLayerStore so the run can
        # see the full Parquet history instead of the control-plane hot window.
        self.research_read_source = research_read_source or settings.research_read_source
        self.research_release_tag = (
            research_release_tag
            if research_release_tag is not None
            else settings.research_release_tag
        )
        self.research_min_trading_days = (
            research_min_trading_days
            if research_min_trading_days is not None
            else settings.research_min_trading_days
        )

    def _warm_read_repository(self) -> WarmResearchReadRepository:
        """Open a warm-layer research read connection bound to a known release."""
        from app.repositories.warm_layer import WarmLayerStore

        store = WarmLayerStore()
        release_tag = self.research_release_tag or store.latest_release_tag
        if release_tag is None:
            raise ValueError(
                "no warm-layer release available; research reads require an "
                "exported Parquet release"
            )
        # Partial releases record their parent; inherit it so tables and dates
        # the release did not publish remain readable.
        base_tag = store.base_release_tag_for(release_tag)
        if base_tag:
            store = WarmLayerStore(base_release_tag=base_tag)
        # Parquet-only on purpose: the ORM session already holds a read-write
        # handle on the same DuckDB file, and DuckDB refuses to attach the file a
        # second time in that situation.  ``market.corporate_action`` and
        # ``market.trading_calendar`` are not slimmed, so they keep being read
        # through the ORM session where they are authoritative.
        connection = store.connect(
            release_tag=release_tag, read_only=True, attach_control=False
        )
        return WarmResearchReadRepository(connection)

    @contextmanager
    def _research_read(
        self, db: Session, *, start_date: date, end_date: date
    ) -> Iterator[tuple[Any, str]]:
        """Yield ``(read_facade, scope_label)`` for research inputs.

        In ``warm`` mode bars, corporate actions and the execution calendar all
        come from one connection bound to one release.  Keeping them on the same
        source matters: reading bars from Parquet while reading corporate
        actions from the control plane would let the corporate-action ``reject``
        gate miss historical events.
        """
        if self.research_read_source == "warm":
            repository = self._warm_read_repository()
            try:
                total_days = repository.total_trading_days()
                if total_days < self.research_min_trading_days:
                    raise ValueError(
                        "warm-layer release exposes only "
                        f"{total_days} trading days, below the "
                        f"{self.research_min_trading_days}-day minimum; refusing "
                        "to run against a truncated release"
                    )
                exported_max = repository.max_exported_date(
                    adjust_type=_EXECUTION_ADJUST_TYPE
                )
                if exported_max is not None and end_date > exported_max:
                    raise ValueError(
                        "backtest window ends after the warm-layer export "
                        f"({end_date} > {exported_max}); re-export the warm "
                        "layer or shorten the window, the control-plane hot "
                        "window is not readable through this connection"
                    )
                yield _WarmReadFacade(repository, self, db), "warm_parquet_only"
            finally:
                repository.close()
        else:
            yield _OrmReadFacade(self, db), "orm_session"

    @contextmanager
    def _research_verify_read(
        self, db: Session, *, start_date: date, end_date: date
    ) -> Iterator[tuple[Any, str]]:
        """Re-open the research inputs after the run for the drift check.

        Warm mode re-opens a fresh connection (which re-reads Parquet), so the
        scope is honestly reported as ``warm_release_reopen`` rather than
        claiming an independent transactional session.
        """
        if self.research_read_source == "warm":
            repository = self._warm_read_repository()
            try:
                exported_max = repository.max_exported_date(
                    adjust_type=_EXECUTION_ADJUST_TYPE
                )
                if exported_max is not None and end_date > exported_max:
                    raise ValueError(
                        "backtest window ends after the warm-layer export "
                        f"({end_date} > {exported_max}); re-export the warm "
                        "layer or shorten the window, the control-plane hot "
                        "window is not readable through this connection"
                    )
                yield _WarmReadFacade(repository, self, db), "warm_parquet_only"
            finally:
                repository.close()
        else:
            with self.verification_session_factory(db) as verification_db:
                scope = (
                    "independent_session"
                    if verification_db is not db
                    else "same_session_fallback"
                )
                yield _OrmReadFacade(self, verification_db), scope


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
        portfolio_constraints: PortfolioConstraints | None = None,
        security_profiles: dict[str, tuple[str, str]] | None = None,
        research_db: Session | None = None,
        quantile_layer_signals: list[list[TargetWeightSignal]] | None = None,
        quantile_meta: "QuantileBacktestMeta | None" = None,
    ) -> PersistedBacktestResult:
        # Strategy artifacts live in the standalone research database; the
        # main session still serves market/calendar reads. ``None`` keeps the
        # single-session behaviour used by tests and legacy callers.
        artifacts_db = research_db if research_db is not None else db
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
            sell_stamp_duty_rate=(
                None if resolved_fee_model_version == A_SHARE_COST_V2
                and sell_stamp_duty_rate is None
                else Decimal(fee_contract["sell_stamp_duty_rate"])
            ),
            transfer_fee_rate=Decimal(fee_contract["transfer_fee_rate"]),
            exchange_fee_rate=Decimal(fee_contract["exchange_fee_rate"]),
        )
        parameters = {
            "adjust_type": adjust_type,
            "execution_adjust_type": _EXECUTION_ADJUST_TYPE,
            "corporate_action_policy": _CORPORATE_ACTION_POLICY,
            "missing_held_bar_policy": "reject_on_open_date_v1",
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
                "missing_held_bar_policy": "reject_on_open_date_v1",
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
            artifacts_db,
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
        benchmark_symbol = (
            research_contract.get("benchmark") if research_contract else None
        )
        try:
            with self._research_read(
                db, start_date=start_date, end_date=end_date
            ) as (reader, read_scope):
                corporate_actions = reader.list_corporate_actions(
                    symbols=symbols, start_date=start_date, end_date=end_date
                )
                if corporate_actions:
                    raise ValueError(
                        "backtest interval contains corporate actions; "
                        "event accounting is not implemented"
                    )
                execution_bars = reader.list_daily_bars(
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=_EXECUTION_ADJUST_TYPE,
                    symbols=symbols,
                    quality_status="trusted",
                )
                execution_calendar = reader.load_execution_calendar(
                    symbols=symbols, start_date=start_date, end_date=end_date
                )
                benchmark_bars = (
                    reader.list_daily_bars(
                        start_date=start_date, end_date=end_date,
                        adjust_type=adjust_type, symbols=[benchmark_symbol.upper()],
                        quality_status="trusted",
                    ) if benchmark_symbol else []
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
                artifacts_db,
                run=run,
                data_snapshot=data_snapshot,
            )
            result = DailyEventBacktestEngine(
                initial_cash=initial_cash,
                lot_size=lot_size,
                fee_model=fee_model,
                portfolio_constraints=portfolio_constraints,
                security_profiles=security_profiles,
            ).run(
                bars=execution_bars,
                signals=normalized_signals,
                execution_calendar=execution_calendar,
            )
            equity_dates = [point.trade_date for point in result.equity_curve]
            benchmark_snapshot = (
                build_benchmark_snapshot(bars=benchmark_bars, equity_dates=equity_dates)
                if benchmark_symbol else None
            )
            with self._research_verify_read(
                db, start_date=start_date, end_date=end_date
            ) as (
                verification_reader,
                verification_scope,
            ):
                corporate_actions_after = verification_reader.list_corporate_actions(
                    symbols=symbols, start_date=start_date, end_date=end_date
                )
                execution_bars_after = verification_reader.list_daily_bars(
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=_EXECUTION_ADJUST_TYPE,
                    symbols=symbols,
                    quality_status="trusted",
                )
                execution_calendar_after = verification_reader.load_execution_calendar(
                    symbols=symbols, start_date=start_date, end_date=end_date
                )
                benchmark_bars_after = (
                    verification_reader.list_daily_bars(
                        start_date=start_date, end_date=end_date,
                        adjust_type=adjust_type, symbols=[benchmark_symbol.upper()],
                        quality_status="trusted",
                    ) if benchmark_symbol else []
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
                artifacts_db,
                run=run,
                data_snapshot=data_snapshot,
            )
            if verification_status == "changed":
                raise BacktestDataChangedError(
                    "backtest_data_changed: trusted market data changed while "
                    "the backtest was running"
                )
            if benchmark_symbol:
                benchmark_snapshot_after = build_benchmark_snapshot(
                    bars=benchmark_bars_after, equity_dates=equity_dates,
                )
                if benchmark_snapshot_after != benchmark_snapshot:
                    raise BacktestDataChangedError(
                        "backtest_data_changed: benchmark data changed during the run"
                    )
                self.backtest_repository.update_run_parameters(
                    artifacts_db, run=run,
                    extra={"benchmark_snapshot": benchmark_snapshot},
                )
            if data_version in {"auto", "workbench"}:
                resolved_data_version = build_input_data_version(
                    market_snapshot=snapshot_before,
                    strategy_input_snapshot=(
                        research_contract.get("strategy_input_snapshot")
                        if research_contract else None
                    ),
                    benchmark_snapshot=benchmark_snapshot,
                )
                run.data_version = resolved_data_version
                self.backtest_repository.update_run_parameters(
                    artifacts_db,
                    run=run,
                    extra={"data_version": resolved_data_version},
                )
            self.backtest_repository.save_result(artifacts_db, run=run, result=result)
            if result.execution_events:
                self.backtest_repository.update_run_parameters(
                    artifacts_db,
                    run=run,
                    extra={"execution_event_summary": {
                        "version": "daily_execution_events_v1",
                        "total": len(result.execution_events),
                        "items": [
                            {
                                "signal_date": event.signal_date.isoformat(),
                                "trade_date": event.trade_date.isoformat(),
                                "symbol": event.symbol,
                                "side": event.side,
                                "status": event.status,
                                "reason": event.reason,
                                "next_execution_date": (
                                    event.next_execution_date.isoformat()
                                    if event.next_execution_date else None
                                ),
                            }
                            for event in result.execution_events[:100]
                        ],
                        "truncated": len(result.execution_events) > 100,
                    }},
                )
            quantile_result = self._maybe_run_quantile_layers(
                db=db,
                artifacts_db=artifacts_db,
                run=run,
                start_date=start_date,
                end_date=end_date,
                fee_model=fee_model,
                portfolio_constraints=portfolio_constraints,
                security_profiles=security_profiles,
                initial_cash=initial_cash,
                lot_size=lot_size,
                layer_signals=quantile_layer_signals,
                quantile_meta=quantile_meta,
            )
        except Exception as exc:
            self.backtest_repository.fail_run(
                artifacts_db, run=run, error_message=str(exc)
            )
            artifacts_db.commit()
            raise
        return PersistedBacktestResult(
            run_id=run.id, result=result, quantile=quantile_result
        )

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

    def _maybe_run_quantile_layers(
        self,
        *,
        db: Session,
        artifacts_db: Session,
        run: Any,
        start_date: date,
        end_date: date,
        fee_model: Any,
        portfolio_constraints: PortfolioConstraints | None,
        security_profiles: dict[str, tuple[str, str]] | None,
        initial_cash: Decimal,
        lot_size: int,
        layer_signals: list[list[TargetWeightSignal]] | None,
        quantile_meta: "QuantileBacktestMeta | None",
    ) -> "QuantileBacktestResult | None":
        if layer_signals is None or quantile_meta is None:
            return None
        quantile_data_snapshot: dict[str, Any] | None = None
        try:
            normalized_layer_signals = [
                [
                    TargetWeightSignal(
                        signal_date=signal.signal_date,
                        symbol=signal.symbol.upper(),
                        target_weight=signal.target_weight,
                    )
                    for signal in signals
                ]
                for signals in layer_signals
            ]
            quantile_symbols = sorted(
                {
                    signal.symbol
                    for signals in normalized_layer_signals
                    for signal in signals
                }
            )
            if not quantile_symbols:
                raise ValueError("quantile backtest produced no target symbols")
            with self._research_read(
                db, start_date=start_date, end_date=end_date
            ) as (reader, _read_scope):
                corporate_actions = reader.list_corporate_actions(
                    symbols=quantile_symbols,
                    start_date=start_date,
                    end_date=end_date,
                )
                if corporate_actions:
                    raise ValueError(
                        "quantile backtest interval contains corporate actions; "
                        "event accounting is not implemented"
                    )
                execution_bars = reader.list_daily_bars(
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=_EXECUTION_ADJUST_TYPE,
                    symbols=quantile_symbols,
                    quality_status="trusted",
                )
                execution_calendar = reader.load_execution_calendar(
                    symbols=quantile_symbols,
                    start_date=start_date,
                    end_date=end_date,
                )
            snapshot_before = _build_market_snapshot(
                bars=execution_bars,
                corporate_actions=corporate_actions,
                execution_calendar=execution_calendar,
                start_date=start_date,
                end_date=end_date,
                symbols=quantile_symbols,
            )
            quantile_data_snapshot = {
                **snapshot_before,
                "verification_status": "pending",
            }
            result = self._run_quantile_layers(
                execution_bars=execution_bars,
                execution_calendar=execution_calendar,
                fee_model=fee_model,
                portfolio_constraints=portfolio_constraints,
                security_profiles=security_profiles,
                initial_cash=initial_cash,
                lot_size=lot_size,
                layer_signals=normalized_layer_signals,
                quantile_meta=quantile_meta,
            )
            with self._research_verify_read(
                db, start_date=start_date, end_date=end_date
            ) as (verification_reader, verification_scope):
                corporate_actions_after = verification_reader.list_corporate_actions(
                    symbols=quantile_symbols,
                    start_date=start_date,
                    end_date=end_date,
                )
                execution_bars_after = verification_reader.list_daily_bars(
                    start_date=start_date,
                    end_date=end_date,
                    adjust_type=_EXECUTION_ADJUST_TYPE,
                    symbols=quantile_symbols,
                    quality_status="trusted",
                )
                execution_calendar_after = verification_reader.load_execution_calendar(
                    symbols=quantile_symbols,
                    start_date=start_date,
                    end_date=end_date,
                )
            snapshot_after = _build_market_snapshot(
                bars=execution_bars_after,
                corporate_actions=corporate_actions_after,
                execution_calendar=execution_calendar_after,
                start_date=start_date,
                end_date=end_date,
                symbols=quantile_symbols,
            )
            verification_status = (
                "verified"
                if snapshot_after["sha256"] == snapshot_before["sha256"]
                else "changed"
            )
            quantile_data_snapshot = {
                **snapshot_before,
                "verification_status": verification_status,
                "verification_scope": verification_scope,
                "post_run_sha256": snapshot_after["sha256"],
            }
            if verification_status == "changed":
                raise BacktestDataChangedError(
                    "quantile_backtest_data_changed: trusted market data changed "
                    "while the quantile layers were running"
                )
            self.backtest_repository.update_run_parameters(
                artifacts_db,
                run=run,
                extra={
                    "quantile_backtest": quantile_result_to_json(result),
                    "quantile_data_snapshot": quantile_data_snapshot,
                },
            )
            artifacts_db.commit()
            return result
        except Exception as exc:  # layer run is additive; never fail the main run
            extra: dict[str, Any] = {"quantile_backtest_error": str(exc)}
            if quantile_data_snapshot is not None:
                if quantile_data_snapshot["verification_status"] == "pending":
                    quantile_data_snapshot = {
                        **quantile_data_snapshot,
                        "verification_status": "not_verified",
                    }
                extra["quantile_data_snapshot"] = quantile_data_snapshot
            self.backtest_repository.update_run_parameters(
                artifacts_db,
                run=run,
                extra=extra,
            )
            artifacts_db.commit()
            return None

    def _run_quantile_layers(
        self,
        *,
        execution_bars: list[Any],
        execution_calendar: dict[str, list[date]],
        fee_model: Any,
        portfolio_constraints: PortfolioConstraints | None,
        security_profiles: dict[str, tuple[str, str]] | None,
        initial_cash: Decimal,
        lot_size: int,
        layer_signals: list[list[TargetWeightSignal]],
        quantile_meta: QuantileBacktestMeta,
    ) -> QuantileBacktestResult:
        layers: list[QuantileLayerResult] = []
        top_equity_by_date: dict[date, Decimal] = {}
        bottom_equity_by_date: dict[date, Decimal] = {}
        for layer_index, signals in enumerate(layer_signals):
            if not signals:
                continue
            engine_result = DailyEventBacktestEngine(
                initial_cash=initial_cash,
                lot_size=lot_size,
                fee_model=fee_model,
                portfolio_constraints=portfolio_constraints,
                security_profiles=security_profiles,
            ).run(
                bars=execution_bars,
                signals=signals,
                execution_calendar=execution_calendar,
            )
            performance = BacktestPerformanceAnalyzer().analyze(
                run=_QuantileFakeRun(initial_cash),
                equity_rows=engine_result.equity_curve,
                trade_rows=engine_result.trades,
                position_rows=engine_result.positions,
                industries={},
                style_values=None,
            )
            bounds = quantile_meta.layer_bounds[layer_index]
            date_symbols: dict[date, set[str]] = {}
            for sig in signals:
                if sig.target_weight > 0:
                    date_symbols.setdefault(sig.signal_date, set()).add(sig.symbol)
            universe_size = max(
                (len(group) for group in date_symbols.values()), default=0
            )
            layers.append(
                QuantileLayerResult(
                    layer_index=layer_index,
                    quantile_low=bounds[0],
                    quantile_high=bounds[1],
                    universe_size=universe_size,
                    asset_count=sum(len(group) for group in date_symbols.values()),
                    top_asset_symbols=tuple(
                        sorted({sig.symbol for sig in signals if sig.target_weight > 0})[:10]
                    ),
                    equity_curve=tuple(engine_result.equity_curve),
                    total_return=performance.total_return,
                    annualized_return=performance.annualized_return,
                    maximum_drawdown=performance.maximum_drawdown,
                    sharpe_ratio=performance.sharpe_ratio,
                    turnover_rate=performance.turnover_rate,
                    total_commission=performance.total_commission,
                )
            )
            if layer_index == 0:
                for point in engine_result.equity_curve:
                    bottom_equity_by_date[point.trade_date] = point.total_equity
            if layer_index == quantile_meta.n_layers - 1:
                for point in engine_result.equity_curve:
                    top_equity_by_date[point.trade_date] = point.total_equity
        if len(layers) < 2:
            raise ValueError(
                "quantile backtest produced fewer than two non-empty layers"
            )
        common_dates = sorted(set(top_equity_by_date) & set(bottom_equity_by_date))
        if not common_dates:
            raise ValueError(
                "quantile backtest top and bottom layers share no trade dates"
            )
        spread_curve = tuple(
            (
                trade_date,
                top_equity_by_date[trade_date] - bottom_equity_by_date[trade_date],
            )
            for trade_date in common_dates
        )
        spread_return = layers[-1].total_return - layers[0].total_return
        return QuantileBacktestResult(
            split_factor_name=quantile_meta.split_factor_name,
            split_factor_version=quantile_meta.split_factor_version,
            n_layers=quantile_meta.n_layers,
            layers=tuple(layers),
            long_short_spread_return=spread_return,
            spread_curve=spread_curve,
            declarations=dict(QUANTILE_DECLARATIONS),
        )


@dataclass(frozen=True)
class _QuantileFakeRun:
    initial_cash: Decimal


class _ResearchReadFacade:
    """Common read surface for research inputs regardless of backing store."""

    def list_corporate_actions(
        self, *, symbols: Iterable[str], start_date: date, end_date: date
    ) -> list[Any]:
        raise NotImplementedError

    def list_daily_bars(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str],
        quality_status: str,
    ) -> list[Any]:
        raise NotImplementedError

    def load_execution_calendar(
        self, *, symbols: list[str], start_date: date, end_date: date
    ) -> dict[str, list[date]]:
        raise NotImplementedError


class _WarmReadFacade(_ResearchReadFacade):
    """Bars and factor values from Parquet; reference facts from the ORM.

    Only price history lives in the slimmed warm layer.  Corporate actions and
    the trading calendar are authoritative (and complete) in the control plane,
    so they continue to be read through the session that owns the write handle.
    """

    def __init__(
        self,
        repository: WarmResearchReadRepository,
        service: DailyBacktestService,
        db: Session,
    ) -> None:
        self._repository = repository
        self._orm = _OrmReadFacade(service, db)

    def list_corporate_actions(
        self, *, symbols: Iterable[str], start_date: date, end_date: date
    ) -> list[Any]:
        return self._orm.list_corporate_actions(
            symbols=symbols, start_date=start_date, end_date=end_date
        )

    def list_daily_bars(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str],
        quality_status: str,
    ) -> list[Any]:
        return self._repository.list_daily_bars(
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=symbols,
            quality_status=quality_status,
        )

    def load_execution_calendar(
        self, *, symbols: list[str], start_date: date, end_date: date
    ) -> dict[str, list[date]]:
        return self._orm.load_execution_calendar(
            symbols=symbols, start_date=start_date, end_date=end_date
        )


class _OrmReadFacade(_ResearchReadFacade):
    def __init__(self, service: DailyBacktestService, db: Session) -> None:
        self._service = service
        self._db = db

    def list_corporate_actions(
        self, *, symbols: Iterable[str], start_date: date, end_date: date
    ) -> list[Any]:
        return self._service.corporate_action_repository.list_for_symbols_date_range(
            self._db, symbols=set(symbols), start_date=start_date, end_date=end_date
        )

    def list_daily_bars(
        self,
        *,
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str],
        quality_status: str,
    ) -> list[Any]:
        return self._service.daily_bar_repository.list_for_factor_calculation(
            self._db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=symbols,
            quality_status=quality_status,
        )

    def load_execution_calendar(
        self, *, symbols: list[str], start_date: date, end_date: date
    ) -> dict[str, list[date]]:
        return self._service._load_execution_calendar(
            self._db, symbols=symbols, start_date=start_date, end_date=end_date
        )


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
