from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import DailyBarRepository

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock", "etf", "index")


@dataclass(frozen=True)
class BasicFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    window_days: int


@dataclass(frozen=True)
class BasicFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


BASIC_FACTOR_SPECS = (
    BasicFactorSpec(
        name="ret_1d",
        display_name="1日收益率",
        description="close / previous trusted close - 1",
        inputs=("market.daily_bar.close",),
        window_days=1,
    ),
    BasicFactorSpec(
        name="volatility_5",
        display_name="5日波动率",
        description="最近5个日收益率的总体标准差",
        inputs=("market.daily_bar.close",),
        window_days=5,
    ),
    BasicFactorSpec(
        name="momentum_5",
        display_name="5日动量",
        description="close / close_lag_5 - 1",
        inputs=("market.daily_bar.close",),
        window_days=5,
    ),
    BasicFactorSpec(
        name="ma_gap_5",
        display_name="5日均线偏离",
        description="close / mean(close, 5) - 1",
        inputs=("market.daily_bar.close",),
        window_days=5,
    ),
    BasicFactorSpec(
        name="volume_chg_1d",
        display_name="1日成交量变化",
        description="volume / volume_lag_1 - 1",
        inputs=("market.daily_bar.volume",),
        window_days=1,
    ),
    BasicFactorSpec(
        name="turnover_rate",
        display_name="换手率",
        description="market.daily_bar.turnover_rate",
        inputs=("market.daily_bar.turnover_rate",),
        window_days=1,
    ),
)


class BasicPriceVolumeFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.value_repository = value_repository or FactorValueRepository()

    def run(
        self,
        db: Session,
        *,
        start_date: date,
        end_date: date,
        symbols: list[str] | None = None,
        factor_names: list[str] | None = None,
        adjust_type: str = "qfq",
        code_version: str = "local",
    ) -> BasicFactorRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")

        specs = self._select_specs(factor_names)
        factor_version = f"{FACTOR_VERSION}-{adjust_type}"
        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        max_window = max(spec.window_days for spec in specs)
        input_start_date = start_date - timedelta(days=max_window * 3)
        bars = self.daily_bar_repository.list_for_factor_calculation(
            db,
            start_date=input_start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
            quality_status="trusted",
        )
        bars_by_symbol: dict[str, list[Any]] = defaultdict(list)
        for bar in bars:
            bars_by_symbol[bar.symbol].append(bar)

        rows_by_factor: dict[str, int] = {}
        run_ids: dict[str, int] = {}
        for spec in specs:
            definition = self.definition_repository.ensure(
                db,
                name=spec.name,
                display_name=spec.display_name,
                description=spec.description,
                frequency="daily",
                inputs=list(spec.inputs),
                applicable_security_types=list(SUPPORTED_SECURITY_TYPES),
                window_days=spec.window_days,
                version=factor_version,
                parameters_schema={},
            )
            run = self.run_repository.create(
                db,
                definition=definition,
                parameters={
                    "adjust_type": adjust_type,
                    "quality_status": "trusted",
                    "symbols": normalized_symbols,
                    "target_start_date": start_date.isoformat(),
                    "target_end_date": end_date.isoformat(),
                },
                code_version=code_version,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_trusted_bar_or_required_field",
                    "window_policy": "last_available_trusted_observations",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                start_date=start_date,
                end_date=end_date,
                run_id=run.id,
                factor_version=factor_version,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_factor[spec.name] = rows_written
            run_ids[spec.name] = run.id

        return BasicFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(bars),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(self, factor_names: list[str] | None) -> tuple[BasicFactorSpec, ...]:
        if not factor_names:
            return BASIC_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return BASIC_FACTOR_SPECS
        available = {spec.name for spec in BASIC_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in BASIC_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: BasicFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        start_date: date,
        end_date: date,
        run_id: int,
        factor_version: str,
    ) -> int:
        rows_written = 0
        for symbol, bars in bars_by_symbol.items():
            eligible_bars = [
                bar
                for bar in bars
                if bar.security_type in SUPPORTED_SECURITY_TYPES
            ]
            for index, bar in enumerate(eligible_bars):
                if bar.trade_date < start_date or bar.trade_date > end_date:
                    continue
                value = self._calculate_value(spec.name, eligible_bars, index)
                if value is None:
                    continue
                self.value_repository.upsert_value(
                    db,
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    factor_name=spec.name,
                    factor_version=factor_version,
                    value=value.quantize(VALUE_QUANT),
                    run_id=run_id,
                )
                rows_written += 1
        return rows_written

    def _calculate_value(
        self,
        factor_name: str,
        bars: list[Any],
        index: int,
    ) -> Decimal | None:
        bar = bars[index]
        if factor_name == "ret_1d":
            if index == 0:
                return None
            return self._ratio_change(bar.close, bars[index - 1].close)
        if factor_name == "volume_chg_1d":
            if index == 0:
                return None
            return self._ratio_change(bar.volume, bars[index - 1].volume)
        if factor_name == "turnover_rate":
            return bar.turnover_rate
        if factor_name == "ma_gap_5":
            if index < 4:
                return None
            closes = [item.close for item in bars[index - 4 : index + 1]]
            if any(close is None for close in closes):
                return None
            mean_close = sum(closes, Decimal()) / Decimal(5)
            return self._ratio_change(bar.close, mean_close)
        if factor_name == "momentum_5":
            if index < 5:
                return None
            return self._ratio_change(bar.close, bars[index - 5].close)
        if factor_name == "volatility_5":
            if index < 5:
                return None
            returns = [
                self._ratio_change(bars[position].close, bars[position - 1].close)
                for position in range(index - 4, index + 1)
            ]
            if any(value is None for value in returns):
                return None
            valid_returns = [value for value in returns if value is not None]
            mean_return = sum(valid_returns, Decimal()) / Decimal(5)
            variance = (
                sum(
                    ((value - mean_return) ** 2 for value in valid_returns),
                    Decimal(),
                )
                / Decimal(5)
            )
            with localcontext() as context:
                context.prec = 28
                return variance.sqrt()
        raise ValueError(f"unsupported factor: {factor_name}")

    @staticmethod
    def _ratio_change(
        current: Decimal | None,
        previous: Decimal | None,
    ) -> Decimal | None:
        if current is None or previous is None or previous == 0:
            return None
        return current / previous - Decimal(1)
