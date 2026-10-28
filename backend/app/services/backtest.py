import hashlib
import json
from collections.abc import Callable, Iterable, Iterator
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
from app.repositories.strategy import BacktestPerformanceRepository, BacktestRepository
from app.schemas.common import Page
from app.schemas.strategy import (
    BacktestBenchmarkRead,
    BacktestComparisonItem,
    BacktestDriftRead,
    BacktestEquityRead,
    BacktestFinalHoldingRead,
    BacktestFinalHoldingsRead,
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
            self.backtest_repository.save_result(artifacts_db, run=run, result=result)
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
        performance = self.performance_repository.get_by_run_id(db, run_id=run_id)
        if performance is None:
            raise ValueError("backtest performance not found after analysis")
        return BacktestPerformanceRead.model_validate(performance)
