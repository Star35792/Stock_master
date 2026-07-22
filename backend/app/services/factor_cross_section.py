from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal, localcontext
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorProfileRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import DailyBarRepository, SecurityRepository

VALUE_QUANT = Decimal("0.0000000001")
PROFILE_VERSION = "v1"
FORWARD_RETURN_HORIZONS = (1, 5, 20)


@dataclass(frozen=True)
class CrossSectionRunResult:
    source_factor_name: str
    source_factor_version: str
    profile_rows: int
    rows_by_transform: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_transform.values())


@dataclass(frozen=True)
class QuantileForwardDiagnostics:
    top_return: Decimal | None
    bottom_return: Decimal | None
    long_short_return: Decimal | None
    monotonicity: Decimal | None


class FactorCrossSectionService:
    def __init__(
        self,
        value_repository: FactorValueRepository | None = None,
        profile_repository: FactorProfileRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.value_repository = value_repository or FactorValueRepository()
        self.profile_repository = profile_repository or FactorProfileRepository()
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()

    def run(
        self,
        db: Session,
        *,
        factor_name: str,
        factor_version: str,
        start_date: date,
        end_date: date,
        adjust_type: str,
        code_version: str,
        symbols: list[str] | None = None,
        size_factor_name: str | None = None,
        size_factor_version: str | None = None,
    ) -> CrossSectionRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")
        if bool(size_factor_name) != bool(size_factor_version):
            raise ValueError("size_factor_name and size_factor_version must be provided together")

        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        source_rows = self.value_repository.list_for_cross_section(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            start_date=start_date,
            end_date=end_date,
        )
        if normalized_symbols:
            allowed_symbols = set(normalized_symbols)
            source_rows = [row for row in source_rows if row.symbol in allowed_symbols]

        values_by_date = self._group_values(source_rows)
        all_symbols = {symbol for values in values_by_date.values() for symbol in values}
        industries = self.security_repository.get_industries(db, all_symbols)
        universe_by_date = self.daily_bar_repository.count_trusted_by_date(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
        )
        size_by_date: dict[date, dict[str, Decimal]] = {}
        if size_factor_name and size_factor_version:
            size_rows = self.value_repository.list_for_cross_section(
                db,
                factor_name=size_factor_name,
                factor_version=size_factor_version,
                start_date=start_date,
                end_date=end_date,
            )
            size_by_date = self._group_values(size_rows)

        forward_returns_by_horizon = self._forward_returns_by_horizon(
            db,
            values_by_date=values_by_date,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
        )
        profile_rows = self._write_profiles(
            db,
            factor_name=factor_name,
            factor_version=factor_version,
            values_by_date=values_by_date,
            universe_by_date=universe_by_date,
            forward_returns_by_horizon=forward_returns_by_horizon,
            code_version=code_version,
        )
        transforms = {
            "winsor": self._winsorize,
            "zscore": self._standardize,
            "rank": self._rank,
            "industry_neutral": lambda values: self._industry_neutralize(
                values, industries
            ),
        }
        if size_factor_name:
            transforms["industry_size_neutral"] = lambda values: self._size_neutralize(
                values,
                industries,
                size_by_date,
            )

        rows_by_transform: dict[str, int] = {}
        run_ids: dict[str, int] = {}
        output_version = f"{PROFILE_VERSION}-from-{factor_version}"
        source_definition = self.definition_repository.get_by_name_version(
            db, name=factor_name, version=factor_version
        )
        source_category = (
            source_definition.category
            if source_definition is not None
            else FactorCategory.PRICE_VOLUME_RISK.value
        )
        for transform_name, transform in transforms.items():
            output_name = f"{factor_name}__{transform_name}"
            definition = self.definition_repository.ensure(
                db,
                name=output_name,
                display_name=output_name,
                description=f"{transform_name} transform of {factor_name}",
                category=source_category,
                frequency="daily",
                inputs=[f"factor.{factor_name}"],
                applicable_security_types=["stock", "etf", "index"],
                window_days=1,
                version=output_version,
                dependencies=[factor_name],
                parameters_schema={},
            )
            run = self.run_repository.create(
                db,
                definition=definition,
                parameters={
                    "source_factor_name": factor_name,
                    "source_factor_version": factor_version,
                    "adjust_type": adjust_type,
                    "symbols": normalized_symbols,
                    "size_factor_name": size_factor_name,
                    "size_factor_version": size_factor_version,
                },
                code_version=code_version,
                input_start_date=start_date,
                input_end_date=end_date,
                metadata={"transform": transform_name},
            )
            transformed = transform(values_by_date)
            rows_written = self._write_values(
                db,
                values_by_date=transformed,
                factor_name=output_name,
                factor_version=output_version,
                run_id=run.id,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_transform[transform_name] = rows_written
            run_ids[transform_name] = run.id

        return CrossSectionRunResult(
            source_factor_name=factor_name,
            source_factor_version=factor_version,
            profile_rows=profile_rows,
            rows_by_transform=rows_by_transform,
            run_ids=run_ids,
        )

    @staticmethod
    def _group_values(rows: list[Any]) -> dict[date, dict[str, Decimal]]:
        grouped: dict[date, dict[str, Decimal]] = defaultdict(dict)
        for row in rows:
            grouped[row.trade_date][row.symbol] = row.value
        return dict(grouped)

    def _write_profiles(
        self,
        db: Session,
        *,
        factor_name: str,
        factor_version: str,
        values_by_date: dict[date, dict[str, Decimal]],
        universe_by_date: dict[date, int],
        forward_returns_by_horizon: dict[int, dict[date, dict[str, Decimal]]],
        code_version: str,
    ) -> int:
        rows_written = 0
        previous_top_symbols: set[str] | None = None
        for trade_date in sorted(set(universe_by_date) | set(values_by_date)):
            symbol_values = values_by_date.get(trade_date, {})
            values = list(symbol_values.values())
            universe_size = universe_by_date.get(trade_date, len(values))
            observation_count = len(values)
            missing_count = max(universe_size - observation_count, 0)
            denominator = Decimal(universe_size) if universe_size else Decimal(1)
            mean = self._mean(values)
            standard_deviation = self._standard_deviation(values)
            extreme_count = 0
            if mean is not None and standard_deviation not in {None, Decimal()}:
                extreme_count = sum(
                    abs(value - mean) > standard_deviation * Decimal(3)
                    for value in values
                )
            current_top_symbols = self._top_quantile_symbols(symbol_values)
            top_quantile_turnover = None
            if previous_top_symbols is not None and current_top_symbols:
                retained = len(previous_top_symbols & current_top_symbols)
                top_quantile_turnover = Decimal(1) - (
                    Decimal(retained) / Decimal(len(current_top_symbols))
                )
            forward_returns_1d = forward_returns_by_horizon.get(1, {}).get(
                trade_date, {}
            )
            quantile_diagnostics_1d = self._quantile_forward_diagnostics(
                symbol_values,
                forward_returns_1d,
            )
            self.profile_repository.upsert(
                db,
                factor_name=factor_name,
                factor_version=factor_version,
                trade_date=trade_date,
                universe_size=universe_size,
                observation_count=observation_count,
                missing_count=missing_count,
                coverage_rate=(Decimal(observation_count) / denominator).quantize(VALUE_QUANT),
                missing_rate=(Decimal(missing_count) / denominator).quantize(VALUE_QUANT),
                mean=self._quantize_optional(mean),
                standard_deviation=self._quantize_optional(standard_deviation),
                minimum=self._quantize_optional(min(values) if values else None),
                maximum=self._quantize_optional(max(values) if values else None),
                p01=self._quantize_optional(self._quantile(values, Decimal("0.01"))),
                p99=self._quantize_optional(self._quantile(values, Decimal("0.99"))),
                extreme_count=extreme_count,
                ic_1d=self._quantize_optional(
                    self._information_coefficient(
                        symbol_values,
                        forward_returns_1d,
                    )
                ),
                rank_ic_1d=self._quantize_optional(
                    self._rank_information_coefficient(
                        symbol_values,
                        forward_returns_1d,
                    )
                ),
                ic_5d=self._quantize_optional(
                    self._information_coefficient(
                        symbol_values,
                        forward_returns_by_horizon.get(5, {}).get(trade_date, {}),
                    )
                ),
                rank_ic_5d=self._quantize_optional(
                    self._rank_information_coefficient(
                        symbol_values,
                        forward_returns_by_horizon.get(5, {}).get(trade_date, {}),
                    )
                ),
                ic_20d=self._quantize_optional(
                    self._information_coefficient(
                        symbol_values,
                        forward_returns_by_horizon.get(20, {}).get(trade_date, {}),
                    )
                ),
                rank_ic_20d=self._quantize_optional(
                    self._rank_information_coefficient(
                        symbol_values,
                        forward_returns_by_horizon.get(20, {}).get(trade_date, {}),
                    )
                ),
                top_quantile_forward_return_1d=self._quantize_optional(
                    quantile_diagnostics_1d.top_return
                ),
                bottom_quantile_forward_return_1d=self._quantize_optional(
                    quantile_diagnostics_1d.bottom_return
                ),
                long_short_forward_return_1d=self._quantize_optional(
                    quantile_diagnostics_1d.long_short_return
                ),
                quantile_monotonicity_1d=self._quantize_optional(
                    quantile_diagnostics_1d.monotonicity
                ),
                top_quantile_turnover=self._quantize_optional(top_quantile_turnover),
                code_version=code_version,
            )
            previous_top_symbols = current_top_symbols
            rows_written += 1
        return rows_written

    def _forward_returns_by_horizon(
        self,
        db: Session,
        *,
        values_by_date: dict[date, dict[str, Decimal]],
        start_date: date,
        end_date: date,
        adjust_type: str,
        symbols: list[str] | None,
    ) -> dict[int, dict[date, dict[str, Decimal]]]:
        if not values_by_date:
            return {}
        target_dates = set(values_by_date)
        bars = self.daily_bar_repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=end_date + timedelta(days=45),
            adjust_type=adjust_type,
            symbols=symbols,
            quality_status="trusted",
        )
        bars_by_symbol: dict[str, list[Any]] = defaultdict(list)
        for bar in bars:
            bars_by_symbol[bar.symbol].append(bar)

        returns_by_horizon: dict[int, dict[date, dict[str, Decimal]]] = {
            horizon: defaultdict(dict) for horizon in FORWARD_RETURN_HORIZONS
        }
        for symbol, symbol_bars in bars_by_symbol.items():
            symbol_bars.sort(key=lambda bar: bar.trade_date)
            for index, current_bar in enumerate(symbol_bars):
                if current_bar.trade_date not in target_dates:
                    continue
                for horizon in FORWARD_RETURN_HORIZONS:
                    future_index = index + horizon
                    if future_index >= len(symbol_bars):
                        continue
                    forward_return = self._ratio_change(
                        symbol_bars[future_index].close,
                        current_bar.close,
                    )
                    if forward_return is not None:
                        returns_by_horizon[horizon][current_bar.trade_date][symbol] = (
                            forward_return
                        )
        return {
            horizon: dict(returns_by_date)
            for horizon, returns_by_date in returns_by_horizon.items()
        }

    def _write_values(
        self,
        db: Session,
        *,
        values_by_date: dict[date, dict[str, Decimal]],
        factor_name: str,
        factor_version: str,
        run_id: int,
    ) -> int:
        rows_written = 0
        for trade_date, values in values_by_date.items():
            for symbol, value in values.items():
                self.value_repository.upsert_value(
                    db,
                    symbol=symbol,
                    trade_date=trade_date,
                    factor_name=factor_name,
                    factor_version=factor_version,
                    value=value.quantize(VALUE_QUANT),
                    run_id=run_id,
                )
                rows_written += 1
        return rows_written

    def _winsorize(
        self,
        values_by_date: dict[date, dict[str, Decimal]],
    ) -> dict[date, dict[str, Decimal]]:
        result = {}
        for trade_date, values in values_by_date.items():
            raw_values = list(values.values())
            lower = self._quantile(raw_values, Decimal("0.01"))
            upper = self._quantile(raw_values, Decimal("0.99"))
            if lower is None or upper is None:
                result[trade_date] = {}
                continue
            result[trade_date] = {
                symbol: min(max(value, lower), upper) for symbol, value in values.items()
            }
        return result

    def _standardize(
        self,
        values_by_date: dict[date, dict[str, Decimal]],
    ) -> dict[date, dict[str, Decimal]]:
        result = {}
        for trade_date, values in values_by_date.items():
            mean = self._mean(list(values.values()))
            standard_deviation = self._standard_deviation(list(values.values()))
            if mean is None:
                result[trade_date] = {}
            elif standard_deviation in {None, Decimal()}:
                result[trade_date] = {symbol: Decimal() for symbol in values}
            else:
                result[trade_date] = {
                    symbol: (value - mean) / standard_deviation
                    for symbol, value in values.items()
                }
        return result

    def _rank(
        self,
        values_by_date: dict[date, dict[str, Decimal]],
    ) -> dict[date, dict[str, Decimal]]:
        result = {}
        for trade_date, values in values_by_date.items():
            result[trade_date] = self._rank_values(values)
        return result

    def _industry_neutralize(
        self,
        values_by_date: dict[date, dict[str, Decimal]],
        industries: dict[str, str],
    ) -> dict[date, dict[str, Decimal]]:
        result = {}
        for trade_date, values in values_by_date.items():
            groups: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
            for symbol, value in values.items():
                groups[industries.get(symbol, "UNKNOWN")].append((symbol, value))
            residuals = {}
            for group_values in groups.values():
                mean = self._mean([value for _, value in group_values]) or Decimal()
                residuals.update(
                    {symbol: value - mean for symbol, value in group_values}
                )
            result[trade_date] = residuals
        return result

    def _size_neutralize(
        self,
        values_by_date: dict[date, dict[str, Decimal]],
        industries: dict[str, str],
        size_by_date: dict[date, dict[str, Decimal]],
    ) -> dict[date, dict[str, Decimal]]:
        result = {}
        for trade_date, values in values_by_date.items():
            sizes = size_by_date.get(trade_date, {})
            common_values = {symbol: value for symbol, value in values.items() if symbol in sizes}
            y_demeaned = self._industry_neutralize(
                {trade_date: common_values},
                industries,
            )[trade_date]
            x_demeaned = self._industry_neutralize(
                {trade_date: {symbol: sizes[symbol] for symbol in common_values}},
                industries,
            )[trade_date]
            denominator = sum((value**2 for value in x_demeaned.values()), Decimal())
            beta = (
                sum(
                    (
                        x_demeaned[symbol] * y_demeaned[symbol]
                        for symbol in common_values
                    ),
                    Decimal(),
                )
                / denominator
                if denominator
                else Decimal()
            )
            result[trade_date] = {
                symbol: y_demeaned[symbol] - beta * x_demeaned[symbol]
                for symbol in common_values
            }
        return result

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal()) / Decimal(len(values))

    def _standard_deviation(self, values: list[Decimal]) -> Decimal | None:
        mean = self._mean(values)
        if mean is None:
            return None
        variance = (
            sum(((value - mean) ** 2 for value in values), Decimal())
            / Decimal(len(values))
        )
        with localcontext() as context:
            context.prec = 28
            return variance.sqrt()

    def _information_coefficient(
        self,
        factor_values: dict[str, Decimal],
        forward_returns: dict[str, Decimal],
    ) -> Decimal | None:
        common_symbols = sorted(set(factor_values) & set(forward_returns))
        if len(common_symbols) < 2:
            return None
        return self._correlation(
            [factor_values[symbol] for symbol in common_symbols],
            [forward_returns[symbol] for symbol in common_symbols],
        )

    def _rank_information_coefficient(
        self,
        factor_values: dict[str, Decimal],
        forward_returns: dict[str, Decimal],
    ) -> Decimal | None:
        common_symbols = sorted(set(factor_values) & set(forward_returns))
        if len(common_symbols) < 2:
            return None
        factor_ranks = self._rank_values(
            {symbol: factor_values[symbol] for symbol in common_symbols}
        )
        return_ranks = self._rank_values(
            {symbol: forward_returns[symbol] for symbol in common_symbols}
        )
        return self._correlation(
            [factor_ranks[symbol] for symbol in common_symbols],
            [return_ranks[symbol] for symbol in common_symbols],
        )

    def _quantile_forward_diagnostics(
        self,
        factor_values: dict[str, Decimal],
        forward_returns: dict[str, Decimal],
    ) -> QuantileForwardDiagnostics:
        common = [
            (symbol, factor_values[symbol], forward_returns[symbol])
            for symbol in sorted(set(factor_values) & set(forward_returns))
        ]
        if len(common) < 2:
            return QuantileForwardDiagnostics(None, None, None, None)

        ordered = sorted(common, key=lambda item: (item[1], item[0]))
        quantile_count = max(
            1,
            int(
                (Decimal(len(ordered)) * Decimal("0.2")).to_integral_value(
                    rounding=ROUND_CEILING
                )
            ),
        )
        bottom_return = self._mean([item[2] for item in ordered[:quantile_count]])
        top_return = self._mean([item[2] for item in ordered[-quantile_count:]])
        long_short_return = (
            None
            if top_return is None or bottom_return is None
            else top_return - bottom_return
        )
        monotonicity = self._quantile_monotonicity(ordered)
        return QuantileForwardDiagnostics(
            top_return=top_return,
            bottom_return=bottom_return,
            long_short_return=long_short_return,
            monotonicity=monotonicity,
        )

    def _quantile_monotonicity(
        self,
        ordered_values: list[tuple[str, Decimal, Decimal]],
        bucket_count: int = 5,
    ) -> Decimal | None:
        count = len(ordered_values)
        if count < 2:
            return None
        actual_bucket_count = min(bucket_count, count)
        buckets: list[list[Decimal]] = [[] for _ in range(actual_bucket_count)]
        for index, (_, _, forward_return) in enumerate(ordered_values):
            bucket_index = index * actual_bucket_count // count
            buckets[bucket_index].append(forward_return)
        bucket_returns = [self._mean(bucket) for bucket in buckets if bucket]
        if len(bucket_returns) < 2 or any(value is None for value in bucket_returns):
            return None
        return self._correlation(
            [Decimal(index) for index in range(len(bucket_returns))],
            [value for value in bucket_returns if value is not None],
        )

    def _correlation(self, left: list[Decimal], right: list[Decimal]) -> Decimal | None:
        left_mean = self._mean(left)
        right_mean = self._mean(right)
        if left_mean is None or right_mean is None:
            return None
        covariance = sum(
            ((left_value - left_mean) * (right_value - right_mean))
            for left_value, right_value in zip(left, right, strict=True)
        )
        left_sum_squares = sum(((value - left_mean) ** 2 for value in left), Decimal())
        right_sum_squares = sum(((value - right_mean) ** 2 for value in right), Decimal())
        if not left_sum_squares or not right_sum_squares:
            return None
        with localcontext() as context:
            context.prec = 28
            denominator = (left_sum_squares * right_sum_squares).sqrt()
            return covariance / denominator

    @staticmethod
    def _rank_values(values: dict[str, Decimal]) -> dict[str, Decimal]:
        ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
        count = len(ordered)
        if count == 0:
            return {}
        if count == 1:
            return {ordered[0][0]: Decimal("0.5")}
        ranks: dict[str, Decimal] = {}
        index = 0
        while index < count:
            end = index
            while end + 1 < count and ordered[end + 1][1] == ordered[index][1]:
                end += 1
            average_rank = Decimal(index + end) / Decimal(2)
            percentile = average_rank / Decimal(count - 1)
            for position in range(index, end + 1):
                ranks[ordered[position][0]] = percentile
            index = end + 1
        return ranks

    @staticmethod
    def _top_quantile_symbols(
        values: dict[str, Decimal],
        quantile: Decimal = Decimal("0.2"),
    ) -> set[str]:
        if not values:
            return set()
        count = max(
            1,
            int((Decimal(len(values)) * quantile).to_integral_value(rounding=ROUND_CEILING)),
        )
        ordered = sorted(values.items(), key=lambda item: (-item[1], item[0]))
        return {symbol for symbol, _ in ordered[:count]}

    @staticmethod
    def _quantile(values: list[Decimal], quantile: Decimal) -> Decimal | None:
        if not values:
            return None
        ordered = sorted(values)
        if len(ordered) == 1:
            return ordered[0]
        position = quantile * Decimal(len(ordered) - 1)
        lower_index = int(position)
        upper_index = min(lower_index + 1, len(ordered) - 1)
        weight = position - Decimal(lower_index)
        return ordered[lower_index] * (Decimal(1) - weight) + ordered[upper_index] * weight

    @staticmethod
    def _quantize_optional(value: Decimal | None) -> Decimal | None:
        return value.quantize(VALUE_QUANT) if value is not None else None

    @staticmethod
    def _ratio_change(
        current: Decimal | None,
        previous: Decimal | None,
    ) -> Decimal | None:
        if current is None or previous is None or previous == 0:
            return None
        return current / previous - Decimal(1)
