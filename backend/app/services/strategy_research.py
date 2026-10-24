import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.backtest.rebalance import select_rebalance_dates
from app.config.settings import settings
from app.portfolio import PortfolioConstraints
from app.repositories.factor import FactorValueRepository
from app.repositories.market import SecurityRepository, TradingCalendarRepository
from app.repositories.research_read import WarmResearchReadRepository
from app.repositories.strategy import TrainingArtifactRepository
from app.schemas.strategy import RollingWindowRead
from app.services.backtest import DailyBacktestService, PersistedBacktestResult
from app.services.rolling_backtest import RollingBacktestWindowResult
from app.strategies import (
    FactorScoreSpec,
    MinimumScorePolicy,
    MultiFactorStrategy,
    StrategyFactorFrame,
    WeightingMode,
)


class StrategyResearchService:
    def __init__(
        self,
        factor_value_repository: FactorValueRepository | None = None,
        security_repository: SecurityRepository | None = None,
        trading_calendar_repository: TradingCalendarRepository | None = None,
        backtest_service: DailyBacktestService | None = None,
        training_artifact_repository: TrainingArtifactRepository | None = None,
    ) -> None:
        self.factor_value_repository = factor_value_repository or FactorValueRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.backtest_service = backtest_service or DailyBacktestService()
        self.training_artifact_repository = (
            training_artifact_repository or TrainingArtifactRepository()
        )
        # Research reads follow the same source policy as the backtest service,
        # otherwise the strategy would still be limited to the control-plane
        # hot window even though history lives in the warm layer. ``getattr``
        # keeps stand-in services used by tests working.
        self.research_read_source = (
            getattr(backtest_service, "research_read_source", None)
            or settings.research_read_source
        )
        self.research_release_tag = (
            getattr(backtest_service, "research_release_tag", None)
            or settings.research_release_tag
        )
        self.research_min_trading_days = (
            getattr(backtest_service, "research_min_trading_days", None)
            or settings.research_min_trading_days
        )

    @contextmanager
    def _factor_read(self, db: Session) -> Iterator[Any]:
        """Yield a factor-row reader bound to the configured research source.

        Warm mode also validates that the bound release still carries its full
        history; a release that silently lost its history would otherwise make
        every factor look like it has no observations.
        """
        if self.research_read_source == "warm":
            from app.repositories.warm_layer import WarmLayerStore

            store = WarmLayerStore()
            release_tag = self.research_release_tag or store.latest_release_tag
            if release_tag is None:
                raise ValueError(
                    "no warm-layer release available; research reads require an "
                    "exported Parquet release"
                )
            base_tag = store.base_release_tag_for(release_tag)
            if base_tag:
                store = WarmLayerStore(base_release_tag=base_tag)
            reader = WarmResearchReadRepository(
                store.connect(
                    release_tag=release_tag,
                    read_only=True,
                    attach_control=False,
                )
            )
            try:
                total_days = reader.total_trading_days()
                minimum = self.research_min_trading_days
                if total_days < minimum:
                    raise ValueError(
                        f"warm-layer release exposes only {total_days} trading "
                        f"days, below the {minimum}-day minimum; refusing to "
                        "read factors from a truncated release"
                    )
                yield reader.list_factor_values
            finally:
                reader.close()
            return

        def read_factor_rows(
            factor_name: str,
            factor_version: str,
            start_date: date,
            end_date: date,
        ) -> Any:
            return self.factor_value_repository.list_for_cross_section(
                db,
                factor_name=factor_name,
                factor_version=factor_version,
                start_date=start_date,
                end_date=end_date,
            )

        yield read_factor_rows

    def generate_rolling_windows(
        self,
        db: Session,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
        training_days: int,
        test_days: int,
        step_days: int,
    ) -> list[RollingWindowRead]:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        open_dates = self.trading_calendar_repository.list_open_dates(
            db,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
        )
        available_starts = len(open_dates) - training_days - test_days
        window_count = available_starts // step_days + 1 if available_starts >= 0 else 0
        if window_count > 20:
            raise ValueError("rolling window count must not exceed 20")
        windows = []
        training_start_index = 0
        while training_start_index + training_days + test_days <= len(open_dates):
            training_end_index = training_start_index + training_days - 1
            test_start_index = training_end_index + 1
            test_end_index = test_start_index + test_days - 1
            windows.append(
                RollingWindowRead(
                    index=len(windows),
                    window_id=(
                        f"rolling:{exchange}:"
                        f"{open_dates[training_start_index].isoformat()}:"
                        f"{open_dates[training_end_index].isoformat()}:"
                        f"{open_dates[test_start_index].isoformat()}:"
                        f"{open_dates[test_end_index].isoformat()}"
                    ),
                    training_start_date=open_dates[training_start_index],
                    training_end_date=open_dates[training_end_index],
                    test_start_date=open_dates[test_start_index],
                    test_end_date=open_dates[test_end_index],
                )
            )
            training_start_index += step_days
        if not windows:
            raise ValueError("open trading dates are insufficient for one rolling window")
        return windows

    def _normalize_factor_specs(
        self, factor_specs: list[FactorScoreSpec]
    ) -> list[FactorScoreSpec]:
        if not factor_specs:
            raise ValueError("factor_specs must not be empty")
        return sorted(
            factor_specs,
            key=lambda spec: (
                spec.factor_name,
                spec.factor_version,
                str(spec.weight),
                spec.higher_is_better,
            ),
        )

    def _load_factor_rows(
        self,
        db: Session,
        factor_specs: list[FactorScoreSpec],
        start_date: date,
        end_date: date,
    ) -> dict[FactorScoreSpec, list[SimpleNamespace]]:
        with self._factor_read(db) as read_factor_rows:
            factor_rows = {
                spec: self._normalize_factor_rows(
                    read_factor_rows(
                        factor_name=spec.factor_name,
                        factor_version=spec.factor_version,
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
                for spec in factor_specs
            }
        for spec, rows in factor_rows.items():
            if not rows:
                raise ValueError(
                    f"factor values not found for {spec.factor_name}@{spec.factor_version} "
                    f"between {start_date.isoformat()} and {end_date.isoformat()}"
                )
        return factor_rows

    @staticmethod
    def _compute_artifact_hash(snapshot: dict[str, Any]) -> str:
        canonical = json.dumps(
            snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _build_training_input_snapshot(
        self,
        *,
        window_id: str,
        factor_specs: list[FactorScoreSpec],
        training_start_date: date,
        training_end_date: date,
        training_rows: dict[FactorScoreSpec, list[SimpleNamespace]],
        security_types: dict[str, str],
    ) -> dict[str, Any]:
        frame = StrategyFactorFrame.from_rows(
            as_of_date=training_end_date,
            rows_by_factor=training_rows,
        )
        frame_snapshot = frame.snapshot()
        return {
            "contract_version": "rolling_training_input_v1",
            "window_id": window_id,
            "training_start_date": training_start_date.isoformat(),
            "training_end_date": training_end_date.isoformat(),
            "factor_specs": [
                {
                    "factor_name": spec.factor_name,
                    "factor_version": spec.factor_version,
                    "weight": str(spec.weight),
                    "higher_is_better": spec.higher_is_better,
                }
                for spec in factor_specs
            ],
            "factor_frame": frame_snapshot,
            "symbol_universe": sorted(
                {row.symbol for rows in training_rows.values() for row in rows}
            ),
            "security_type_map": {
                symbol.upper(): security_type
                for symbol, security_type in security_types.items()
            },
        }

    def _get_or_create_training_artifact(
        self,
        db: Session,
        *,
        window: RollingWindowRead,
        strategy_name: str,
        factor_specs: list[FactorScoreSpec],
        training_rows: dict[FactorScoreSpec, list[SimpleNamespace]],
        security_types: dict[str, str],
        code_version: str,
        data_version: str,
    ) -> Any:
        existing = self.training_artifact_repository.get_by_window_id(
            db, window_id=window.window_id
        )
        snapshot = self._build_training_input_snapshot(
            window_id=window.window_id,
            factor_specs=factor_specs,
            training_start_date=window.training_start_date,
            training_end_date=window.training_end_date,
            training_rows=training_rows,
            security_types=security_types,
        )
        artifact_hash = self._compute_artifact_hash(snapshot)
        if existing is not None:
            if existing.artifact_hash != artifact_hash:
                raise ValueError(
                    f"training artifact for window {window.window_id} already exists "
                    "but its content hash does not match the current training input; "
                    "factor data may have been backfilled or changed"
                )
            return existing
        return self.training_artifact_repository.create(
            db,
            window_id=window.window_id,
            strategy_name=strategy_name,
            factor_specs=snapshot["factor_specs"],
            training_start_date=window.training_start_date,
            training_end_date=window.training_end_date,
            test_start_date=window.test_start_date,
            test_end_date=window.test_end_date,
            training_input_snapshot=snapshot,
            artifact_hash=artifact_hash,
            code_version=code_version,
            data_version=data_version,
        )

    def _run_single_factor_selection(
        self,
        db: Session,
        *,
        name: str,
        strategy_name: str,
        factor_specs: list[FactorScoreSpec],
        top_n: int,
        signal_lag: int,
        rebalance_rule: str,
        holding_period: int,
        rebalance_interval: int = 1,
        portfolio_constraints: PortfolioConstraints | None = None,
        benchmark: str | None,
        allowed_security_types: set[str],
        coverage_ratio: Decimal | None,
        weighting_mode: WeightingMode,
        minimum_score_policy: MinimumScorePolicy,
        minimum_score: Decimal | None,
        invested_weight: Decimal,
        start_date: date,
        end_date: date,
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        code_version: str,
        data_version: str,
        validation_mode: str,
        training_start_date: date | None,
        training_end_date: date | None,
        fee_model_version: str | None,
        min_commission: Decimal | None,
        sell_stamp_duty_rate: Decimal | None,
        transfer_fee_rate: Decimal | None,
        exchange_fee_rate: Decimal | None,
        training_artifact_id: int | None = None,
        validation_window_id: str | None = None,
    ) -> PersistedBacktestResult:
        self._validate_validation_window(
            validation_mode=validation_mode,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            test_start_date=start_date,
            test_end_date=end_date,
        )
        if signal_lag != 1:
            raise ValueError("only signal_lag=1 is supported")
        if rebalance_interval < 1:
            raise ValueError("rebalance_interval must be >= 1")
        if rebalance_rule != "every_n_trading_days" and rebalance_interval != 1:
            raise ValueError(
                "rebalance_interval only applies to rebalance_rule=every_n_trading_days"
            )
        if holding_period != 1:
            raise ValueError("only holding_period=1 is supported")
        normalized_specs = self._normalize_factor_specs(factor_specs)
        factor_rows = self._load_factor_rows(
            db, normalized_specs, start_date, end_date
        )
        symbols = {row.symbol for rows in factor_rows.values() for row in rows}
        security_types = self.security_repository.get_security_types(db, symbols)
        normalized_security_types = {
            symbol.upper(): security_type
            for symbol, security_type in security_types.items()
        }
        strategy_frame = StrategyFactorFrame.from_rows(
            as_of_date=end_date,
            rows_by_factor=factor_rows,
        )
        decision_dates: set[date] | None = None
        if rebalance_rule != "daily":
            # Non-daily rebalancing decides on the selected rebalance dates
            # only (signals execute T+1 as usual). Passing the dates into the
            # strategy keeps the previous-selection chain on the rebalance
            # calendar, so exits land on retained days instead of being
            # emitted — and silently lost — on filtered-out days.
            open_dates = self.trading_calendar_repository.list_open_dates(
                db,
                exchange="SH",
                start_date=start_date,
                end_date=end_date,
            )
            rebalance_dates = select_rebalance_dates(
                open_dates,
                rule=rebalance_rule,
                interval=rebalance_interval,
            )
            observation_dates = set(strategy_frame.observation_dates)
            decision_dates = rebalance_dates & observation_dates
            if not decision_dates:
                raise ValueError(
                    "rebalance rule leaves no decision date with factor "
                    "observations in the requested window"
                )
        signals = MultiFactorStrategy(
            top_n=top_n,
            allowed_security_types=allowed_security_types,
            coverage_ratio=coverage_ratio,
            weighting_mode=weighting_mode,
            minimum_score_policy=minimum_score_policy,
            minimum_score=minimum_score,
            invested_weight=invested_weight,
        ).generate_history(
            frame=strategy_frame,
            security_types=normalized_security_types,
            decision_dates=sorted(decision_dates) if decision_dates else None,
        )
        security_profiles: dict[str, tuple[str, str]] = {}
        if portfolio_constraints is not None:
            profiles = self.security_repository.get_many(db, symbols=symbols)
            security_profiles = {
                security.symbol.upper(): (
                    security.security_type,
                    security.industry or "unknown",
                )
                for security in profiles
            }
        contract = self._build_research_contract(
            strategy_name=strategy_name,
            factor_specs=normalized_specs,
            top_n=top_n,
            signal_lag=signal_lag,
            rebalance_rule=rebalance_rule,
            rebalance_interval=rebalance_interval,
            holding_period=holding_period,
            portfolio_constraints=portfolio_constraints,
            benchmark=benchmark,
            allowed_security_types=allowed_security_types,
            coverage_ratio=coverage_ratio,
            weighting_mode=weighting_mode,
            minimum_score_policy=minimum_score_policy,
            minimum_score=minimum_score,
            invested_weight=invested_weight,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            lot_size=lot_size,
            fee_model_version=fee_model_version,
            min_commission=min_commission,
            sell_stamp_duty_rate=sell_stamp_duty_rate,
            transfer_fee_rate=transfer_fee_rate,
            exchange_fee_rate=exchange_fee_rate,
            code_version=code_version,
            data_version=data_version,
            validation_mode=validation_mode,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            strategy_input_snapshot=strategy_frame.snapshot(),
            validation_window_id=validation_window_id,
        )
        return self.backtest_service.run(
            db,
            name=name,
            strategy_name=strategy_name,
            signals=signals,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            lot_size=lot_size,
            code_version=code_version,
            data_version=data_version,
            research_contract=contract,
            fee_model_version=fee_model_version,
            min_commission=min_commission,
            sell_stamp_duty_rate=sell_stamp_duty_rate,
            transfer_fee_rate=transfer_fee_rate,
            exchange_fee_rate=exchange_fee_rate,
            training_artifact_id=training_artifact_id,
            portfolio_constraints=portfolio_constraints,
            security_profiles=security_profiles,
        )

    def run_factor_selection(
        self,
        db: Session,
        *,
        name: str,
        strategy_name: str = "factor_selection",
        factor_specs: list[FactorScoreSpec],
        top_n: int,
        signal_lag: int = 1,
        rebalance_rule: str = "daily",
        holding_period: int = 1,
        rebalance_interval: int = 1,
        portfolio_constraints: PortfolioConstraints | None = None,
        benchmark: str | None = None,
        allowed_security_types: set[str],
        coverage_ratio: Decimal | None = None,
        weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED,
        minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED,
        minimum_score: Decimal | None = None,
        invested_weight: Decimal = Decimal("1"),
        start_date: date,
        end_date: date,
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        code_version: str,
        data_version: str,
        validation_mode: str = "none",
        training_start_date: date | None = None,
        training_end_date: date | None = None,
        fee_model_version: str | None = None,
        min_commission: Decimal | None = None,
        sell_stamp_duty_rate: Decimal | None = None,
        transfer_fee_rate: Decimal | None = None,
        exchange_fee_rate: Decimal | None = None,
    ) -> PersistedBacktestResult:
        if validation_mode == "rolling":
            raise ValueError(
                "rolling execution must use run_rolling_factor_selection; "
                "single backtest requests cannot bind a validation window"
            )
        return self._run_single_factor_selection(
            db,
            name=name,
            strategy_name=strategy_name,
            factor_specs=factor_specs,
            top_n=top_n,
            signal_lag=signal_lag,
            rebalance_rule=rebalance_rule,
            holding_period=holding_period,
            rebalance_interval=rebalance_interval,
            portfolio_constraints=portfolio_constraints,
            benchmark=benchmark,
            allowed_security_types=allowed_security_types,
            coverage_ratio=coverage_ratio,
            weighting_mode=weighting_mode,
            minimum_score_policy=minimum_score_policy,
            minimum_score=minimum_score,
            invested_weight=invested_weight,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            lot_size=lot_size,
            code_version=code_version,
            data_version=data_version,
            validation_mode=validation_mode,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            fee_model_version=fee_model_version,
            min_commission=min_commission,
            sell_stamp_duty_rate=sell_stamp_duty_rate,
            transfer_fee_rate=transfer_fee_rate,
            exchange_fee_rate=exchange_fee_rate,
        )

    def run_rolling_factor_selection(
        self,
        db: Session,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
        training_days: int,
        test_days: int,
        step_days: int,
        name: str,
        strategy_name: str = "factor_selection",
        factor_specs: list[FactorScoreSpec],
        top_n: int,
        signal_lag: int = 1,
        rebalance_rule: str = "daily",
        holding_period: int = 1,
        rebalance_interval: int = 1,
        portfolio_constraints: PortfolioConstraints | None = None,
        benchmark: str | None = None,
        allowed_security_types: set[str],
        coverage_ratio: Decimal | None = None,
        weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED,
        minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED,
        minimum_score: Decimal | None = None,
        invested_weight: Decimal = Decimal("1"),
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        code_version: str,
        data_version: str,
        fee_model_version: str | None = None,
        min_commission: Decimal | None = None,
        sell_stamp_duty_rate: Decimal | None = None,
        transfer_fee_rate: Decimal | None = None,
        exchange_fee_rate: Decimal | None = None,
    ) -> list[RollingBacktestWindowResult]:
        if rebalance_rule != "daily" and exchange != "SH":
            # Rebalance-date selection is anchored on the SH calendar; HK-only
            # windows could drop whole rebalance weeks silently. Reject until
            # per-exchange rebalance calendars are implemented.
            raise ValueError(
                "non-daily rebalance rules are only supported for exchange=SH"
            )
        windows = self.generate_rolling_windows(
            db,
            exchange=exchange,
            start_date=start_date,
            end_date=end_date,
            training_days=training_days,
            test_days=test_days,
            step_days=step_days,
        )
        normalized_specs = self._normalize_factor_specs(factor_specs)
        results: list[RollingBacktestWindowResult] = []
        for window in windows:
            training_rows = self._load_factor_rows(
                db,
                normalized_specs,
                window.training_start_date,
                window.training_end_date,
            )
            symbols = {
                row.symbol for rows in training_rows.values() for row in rows
            }
            security_types = self.security_repository.get_security_types(db, symbols)
            artifact = self._get_or_create_training_artifact(
                db,
                window=window,
                strategy_name=strategy_name,
                factor_specs=normalized_specs,
                training_rows=training_rows,
                security_types=security_types,
                code_version=code_version,
                data_version=data_version,
            )
            result = self._run_single_factor_selection(
                db,
                name=f"{name}:{window.index}",
                strategy_name=strategy_name,
                factor_specs=normalized_specs,
                top_n=top_n,
                signal_lag=signal_lag,
                rebalance_rule=rebalance_rule,
                holding_period=holding_period,
                rebalance_interval=rebalance_interval,
                portfolio_constraints=portfolio_constraints,
                benchmark=benchmark,
                allowed_security_types=allowed_security_types,
                coverage_ratio=coverage_ratio,
                weighting_mode=weighting_mode,
                minimum_score_policy=minimum_score_policy,
                minimum_score=minimum_score,
                invested_weight=invested_weight,
                start_date=window.test_start_date,
                end_date=window.test_end_date,
                adjust_type=adjust_type,
                initial_cash=initial_cash,
                commission_rate=commission_rate,
                slippage_bps=slippage_bps,
                lot_size=lot_size,
                code_version=code_version,
                data_version=data_version,
                validation_mode="rolling",
                training_start_date=window.training_start_date,
                training_end_date=window.training_end_date,
                fee_model_version=fee_model_version,
                min_commission=min_commission,
                sell_stamp_duty_rate=sell_stamp_duty_rate,
                transfer_fee_rate=transfer_fee_rate,
                exchange_fee_rate=exchange_fee_rate,
                training_artifact_id=artifact.id,
                validation_window_id=window.window_id,
            )
            results.append(
                RollingBacktestWindowResult(
                    window=RollingWindowRead(
                        index=window.index,
                        window_id=window.window_id,
                        training_start_date=window.training_start_date,
                        training_end_date=window.training_end_date,
                        test_start_date=window.test_start_date,
                        test_end_date=window.test_end_date,
                        execution_status="run_completed",
                    ),
                    persisted=result,
                )
            )
        return results

    @staticmethod
    def _normalize_factor_rows(rows: list[Any]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                trade_date=row.trade_date,
                symbol=row.symbol.upper(),
                value=row.value,
                run_id=getattr(row, "run_id", None),
            )
            for row in rows
        ]

    @staticmethod
    def _optional_decimal_str(value: Decimal | None) -> str | None:
        return str(value) if value is not None else None

    @staticmethod
    def _build_research_contract(
        *,
        strategy_name: str,
        factor_specs: list[FactorScoreSpec],
        top_n: int,
        signal_lag: int,
        rebalance_rule: str,
        rebalance_interval: int,
        holding_period: int,
        portfolio_constraints: PortfolioConstraints | None,
        benchmark: str | None,
        allowed_security_types: set[str],
        coverage_ratio: Decimal | None,
        weighting_mode: WeightingMode,
        minimum_score_policy: MinimumScorePolicy,
        minimum_score: Decimal | None,
        invested_weight: Decimal,
        start_date: date,
        end_date: date,
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        fee_model_version: str,
        min_commission: Decimal | None,
        sell_stamp_duty_rate: Decimal | None,
        transfer_fee_rate: Decimal | None,
        exchange_fee_rate: Decimal | None,
        code_version: str,
        data_version: str,
        validation_mode: str,
        training_start_date: date | None,
        training_end_date: date | None,
        strategy_input_snapshot: dict[str, Any],
        validation_window_id: str | None = None,
    ) -> dict[str, Any]:
        validation_protocol = None
        window_binding_status = "not_applicable"
        if validation_mode == "out_of_sample":
            assert training_start_date is not None
            assert training_end_date is not None
            validation_protocol = "holdout_v1"
            validation_window_id = (
                f"out_of_sample:{training_start_date.isoformat()}:"
                f"{training_end_date.isoformat()}:{start_date.isoformat()}:"
                f"{end_date.isoformat()}"
            )
            window_binding_status = "recorded_only"
        elif validation_mode == "rolling":
            assert training_start_date is not None
            assert training_end_date is not None
            assert validation_window_id is not None
            validation_protocol = "rolling_walk_forward_v1"
            window_binding_status = "artifact_bound"
        return {
            "strategy_name": strategy_name,
            "factor_specs": [
                {
                    "factor_name": spec.factor_name,
                    "factor_version": spec.factor_version,
                    "weight": str(spec.weight),
                    "higher_is_better": spec.higher_is_better,
                }
                for spec in factor_specs
            ],
            "top_n": top_n,
            "signal_lag": signal_lag,
            "rebalance_rule": rebalance_rule,
            "rebalance_interval": rebalance_interval,
            "portfolio_constraints": (
                None
                if portfolio_constraints is None
                else {
                    "max_symbol_weight": str(portfolio_constraints.max_symbol_weight),
                    "max_industry_weight": str(portfolio_constraints.max_industry_weight),
                    "cash_reserve_rate": str(portfolio_constraints.cash_reserve_rate),
                    "security_type_budgets": (
                        None
                        if portfolio_constraints.security_type_budgets is None
                        else {
                            key: str(value)
                            for key, value in portfolio_constraints.security_type_budgets.items()
                        }
                    ),
                    "enforcement_mode": "proportional_scale",
                }
            ),
            "holding_period": holding_period,
            "benchmark": benchmark,
            "allowed_security_types": sorted(allowed_security_types),
            "coverage_ratio": StrategyResearchService._optional_decimal_str(coverage_ratio),
            "weighting_mode": str(weighting_mode),
            "minimum_score_policy": str(minimum_score_policy),
            "minimum_score": StrategyResearchService._optional_decimal_str(minimum_score),
            "invested_weight": str(invested_weight),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "test_start_date": start_date.isoformat(),
            "test_end_date": end_date.isoformat(),
            "adjust_type": adjust_type,
            "initial_cash": str(initial_cash),
            "commission_rate": str(commission_rate),
            "slippage_bps": str(slippage_bps),
            "lot_size": lot_size,
            "fee_model_version": fee_model_version,
            "min_commission": StrategyResearchService._optional_decimal_str(min_commission),
            "sell_stamp_duty_rate": StrategyResearchService._optional_decimal_str(
                sell_stamp_duty_rate
            ),
            "transfer_fee_rate": StrategyResearchService._optional_decimal_str(
                transfer_fee_rate
            ),
            "exchange_fee_rate": StrategyResearchService._optional_decimal_str(
                exchange_fee_rate
            ),
            "code_version": code_version,
            "data_version": data_version,
            "strategy_input_snapshot": strategy_input_snapshot,
            "validation_mode": validation_mode,
            "validation_protocol": validation_protocol,
            "validation_window_id": validation_window_id,
            "training_artifact_verified": False,
            "window_binding_status": window_binding_status,
            "training_start_date": (
                training_start_date.isoformat() if training_start_date else None
            ),
            "training_end_date": training_end_date.isoformat() if training_end_date else None,
        }

    @staticmethod
    def _validate_validation_window(
        *,
        validation_mode: str,
        training_start_date: date | None,
        training_end_date: date | None,
        test_start_date: date,
        test_end_date: date,
    ) -> None:
        if validation_mode not in {"none", "out_of_sample", "rolling"}:
            raise ValueError("validation_mode must be one of: none, out_of_sample, rolling")
        if test_start_date > test_end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if validation_mode == "none":
            if training_start_date is not None or training_end_date is not None:
                raise ValueError("training dates require a validation mode")
            return
        if training_start_date is None or training_end_date is None:
            raise ValueError("training dates are required for validation runs")
        if training_start_date > training_end_date:
            raise ValueError(
                "training_start_date must be earlier than or equal to training_end_date"
            )
        if training_end_date >= test_start_date:
            raise ValueError("training period must end before the test period")
