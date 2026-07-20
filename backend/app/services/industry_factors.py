from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import DailyBarRepository, SecurityRepository

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock",)
LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class IndustryFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    horizon_days: int
    higher_is_better: bool = True


@dataclass(frozen=True)
class IndustryFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


INDUSTRY_FACTOR_SPECS = (
    IndustryFactorSpec(
        name="industry_momentum_60",
        display_name="Industry momentum 60",
        description=(
            "60-trading-day equal-weight industry momentum using current security "
            "industry labels"
        ),
        inputs=("market.daily_bar.close", "market.security.industry"),
        horizon_days=60,
    ),
    IndustryFactorSpec(
        name="industry_breadth_20",
        display_name="Industry breadth 20",
        description=(
            "share of current-industry stocks with positive 20-trading-day return"
        ),
        inputs=("market.daily_bar.close", "market.security.industry"),
        horizon_days=20,
    ),
    IndustryFactorSpec(
        name="industry_turnover_share_change_20",
        display_name="Industry turnover share change 20",
        description=(
            "current-industry total amount share minus its 20-observation lag"
        ),
        inputs=("market.daily_bar.amount", "market.security.industry"),
        horizon_days=20,
    ),
)


class IndustryFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()
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
    ) -> IndustryFactorRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")

        specs = self._select_specs(factor_names)
        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
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
            if bar.security_type in SUPPORTED_SECURITY_TYPES:
                bars_by_symbol[bar.symbol].append(bar)

        all_symbols = set(bars_by_symbol)
        industries = self.security_repository.get_industries(db, all_symbols)
        industry_metrics_by_factor = self._build_industry_metrics_by_factor(
            bars_by_symbol=bars_by_symbol,
            industries=industries,
            specs=specs,
        )

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
                window_days=spec.horizon_days,
                version=FACTOR_VERSION,
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
                    "industry_mapping_source": "current_security.industry",
                    "lookback_days": LOOKBACK_DAYS,
                },
                code_version=code_version,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "mapping_source": "current_security.industry",
                    "horizon_days": spec.horizon_days,
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                industries=industries,
                industry_metrics_by_factor=industry_metrics_by_factor[spec.name],
                run_id=run.id,
                start_date=start_date,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_factor[spec.name] = rows_written
            run_ids[spec.name] = run.id

        return IndustryFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(bars),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(
        self, factor_names: list[str] | None
    ) -> tuple[IndustryFactorSpec, ...]:
        if not factor_names:
            return INDUSTRY_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return INDUSTRY_FACTOR_SPECS
        available = {spec.name for spec in INDUSTRY_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in INDUSTRY_FACTOR_SPECS if spec.name in requested)

    def _build_industry_metrics_by_factor(
        self,
        *,
        bars_by_symbol: dict[str, list[Any]],
        industries: dict[str, str],
        specs: tuple[IndustryFactorSpec, ...],
    ) -> dict[str, dict[date, dict[str, Decimal]]]:
        result: dict[str, dict[date, dict[str, Decimal]]] = {}
        turnover_specs = {
            spec.name: spec
            for spec in specs
            if spec.name == "industry_turnover_share_change_20"
        }
        for spec in turnover_specs.values():
            result[spec.name] = self._build_industry_turnover_share_change(
                bars_by_symbol=bars_by_symbol,
                industries=industries,
                horizon_days=spec.horizon_days,
            )

        horizons = {spec.horizon_days for spec in specs}
        returns_by_horizon = {
            horizon: self._build_symbol_returns(bars_by_symbol, horizon_days=horizon)
            for horizon in horizons
        }
        for spec in specs:
            if spec.name in turnover_specs:
                continue
            industry_values_by_date: dict[date, dict[str, list[Decimal]]] = defaultdict(
                lambda: defaultdict(list)
            )
            symbol_returns = returns_by_horizon[spec.horizon_days]
            for symbol, dates in symbol_returns.items():
                industry = industries.get(symbol)
                if not industry:
                    continue
                for trade_date, value in dates.items():
                    industry_values_by_date[trade_date][industry].append(value)

            result[spec.name] = {}
            for trade_date, industry_values in industry_values_by_date.items():
                result[spec.name][trade_date] = {}
                for industry, values in industry_values.items():
                    if spec.name == "industry_momentum_60":
                        metric = sum(values, Decimal()) / Decimal(len(values))
                    elif spec.name == "industry_breadth_20":
                        metric = (
                            Decimal(sum(value > 0 for value in values))
                            / Decimal(len(values))
                        )
                    else:
                        raise ValueError(f"unsupported factor: {spec.name}")
                    result[spec.name][trade_date][industry] = metric
        return result

    @staticmethod
    def _build_industry_turnover_share_change(
        *,
        bars_by_symbol: dict[str, list[Any]],
        industries: dict[str, str],
        horizon_days: int,
    ) -> dict[date, dict[str, Decimal]]:
        industry_amount_by_date: dict[date, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        total_amount_by_date: dict[date, Decimal] = defaultdict(Decimal)
        for symbol, bars in bars_by_symbol.items():
            industry = industries.get(symbol)
            if not industry:
                continue
            for bar in bars:
                if bar.amount is None:
                    continue
                industry_amount_by_date[bar.trade_date][industry] += bar.amount
                total_amount_by_date[bar.trade_date] += bar.amount

        shares_by_date: dict[date, dict[str, Decimal]] = {}
        for trade_date, industry_amounts in industry_amount_by_date.items():
            total_amount = total_amount_by_date[trade_date]
            if total_amount <= 0:
                continue
            shares_by_date[trade_date] = {
                industry: amount / total_amount
                for industry, amount in industry_amounts.items()
            }

        ordered_dates = sorted(shares_by_date)
        result: dict[date, dict[str, Decimal]] = {}
        for index in range(horizon_days, len(ordered_dates)):
            trade_date = ordered_dates[index]
            lag_date = ordered_dates[index - horizon_days]
            current_shares = shares_by_date[trade_date]
            lag_shares = shares_by_date[lag_date]
            result[trade_date] = {
                industry: share - lag_shares[industry]
                for industry, share in current_shares.items()
                if industry in lag_shares
            }
        return result

    @staticmethod
    def _build_symbol_returns(
        bars_by_symbol: dict[str, list[Any]],
        *,
        horizon_days: int,
    ) -> dict[str, dict[date, Decimal]]:
        result: dict[str, dict[date, Decimal]] = {}
        for symbol, bars in bars_by_symbol.items():
            if len(bars) <= horizon_days:
                continue
            symbol_returns: dict[date, Decimal] = {}
            for index in range(horizon_days, len(bars)):
                current = bars[index]
                previous = bars[index - horizon_days]
                if current.close is None or previous.close in {None, Decimal()}:
                    continue
                if previous.close == 0:
                    continue
                symbol_returns[current.trade_date] = current.close / previous.close - Decimal(1)
            if symbol_returns:
                result[symbol] = symbol_returns
        return result

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: IndustryFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        industries: dict[str, str],
        industry_metrics_by_factor: dict[date, dict[str, Decimal]],
        run_id: int,
        start_date: date,
    ) -> int:
        rows_written = 0
        for symbol, bars in bars_by_symbol.items():
            industry = industries.get(symbol)
            if not industry:
                continue
            for bar in bars:
                if bar.trade_date < start_date:
                    continue
                value = industry_metrics_by_factor.get(bar.trade_date, {}).get(industry)
                if value is None:
                    continue
                self.value_repository.upsert_value(
                    db,
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    factor_name=spec.name,
                    factor_version=FACTOR_VERSION,
                    value=value.quantize(VALUE_QUANT),
                    run_id=run_id,
                )
                rows_written += 1
        return rows_written
