from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.factor import FactorValueRepository
from app.repositories.market import SecurityRepository, TradingCalendarRepository
from app.schemas.strategy import RollingWindowRead
from app.services.backtest import DailyBacktestService, PersistedBacktestResult
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
    ) -> None:
        self.factor_value_repository = factor_value_repository or FactorValueRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )
        self.backtest_service = backtest_service or DailyBacktestService()

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
        self._validate_validation_window(
            validation_mode=validation_mode,
            training_start_date=training_start_date,
            training_end_date=training_end_date,
            test_start_date=start_date,
            test_end_date=end_date,
        )
        if not factor_specs:
            raise ValueError("factor_specs must not be empty")
        if signal_lag != 1:
            raise ValueError("only signal_lag=1 is supported")
        if rebalance_rule != "daily":
            raise ValueError("only rebalance_rule=daily is supported")
        if holding_period != 1:
            raise ValueError("only holding_period=1 is supported")
        normalized_specs = sorted(
            factor_specs,
            key=lambda spec: (
                spec.factor_name,
                spec.factor_version,
                str(spec.weight),
                spec.higher_is_better,
            ),
        )
        factor_rows = {
            spec: self._normalize_factor_rows(
                self.factor_value_repository.list_for_cross_section(
                    db,
                    factor_name=spec.factor_name,
                    factor_version=spec.factor_version,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            for spec in normalized_specs
        }
        for spec, rows in factor_rows.items():
            if not rows:
                raise ValueError(
                    f"factor values not found for {spec.factor_name}@{spec.factor_version}"
                )
        symbols = {
            row.symbol
            for rows in factor_rows.values()
            for row in rows
        }
        security_types = self.security_repository.get_security_types(db, symbols)
        normalized_security_types = {
            symbol.upper(): security_type
            for symbol, security_type in security_types.items()
        }
        strategy_frame = StrategyFactorFrame.from_rows(
            as_of_date=end_date,
            rows_by_factor=factor_rows,
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
        )
        contract = self._build_research_contract(
            strategy_name=strategy_name,
            factor_specs=normalized_specs,
            top_n=top_n,
            signal_lag=signal_lag,
            rebalance_rule=rebalance_rule,
            holding_period=holding_period,
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
        )

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
        holding_period: int,
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
    ) -> dict[str, Any]:
        validation_window_id = None
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
        if validation_mode == "rolling":
            raise ValueError(
                "rolling execution requires immutable training artifacts; "
                "the rolling-windows endpoint is planning-only"
            )
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
