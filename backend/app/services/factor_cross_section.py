from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.factors import rank_ic
from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorProfileRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import (
    DailyBarRepository,
    SecurityRepository,
    TradingCalendarRepository,
)

VALUE_QUANT = Decimal("0.0000000001")
PROFILE_VERSION = "v1"
FORWARD_RETURN_HORIZONS = (1, 5, 20)
FORWARD_CALENDAR_LOOKAHEAD_DAYS = 90


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
        trading_calendar_repository: TradingCalendarRepository | None = None,
    ) -> None:
        self.value_repository = value_repository or FactorValueRepository()
        self.profile_repository = profile_repository or FactorProfileRepository()
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.trading_calendar_repository = (
            trading_calendar_repository or TradingCalendarRepository()
        )

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
        release_id: str | None = None,
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
            "industry_neutral": lambda values: self._industry_neutralize(values, industries),
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
                release_id=release_id,
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
        """Persist the daily cross-section profiles.

        **Fail-closed on empty cross-sections.**  A trading day on which the
        factor produced no observation at all carries no evidence, but writing
        it would *replace* an existing, valid row for the same
        ``(factor_name, factor_version, trade_date)`` key -- and that is how a
        batch run over a factor whose values are absent from the warm layer
        silently destroys the IC evidence another script produced.  Such days
        are therefore skipped rather than written.
        """
        rows_written = 0
        previous_top_symbols: set[str] | None = None
        for trade_date in sorted(set(universe_by_date) | set(values_by_date)):
            symbol_values = values_by_date.get(trade_date, {})
            values = list(symbol_values.values())
            universe_size = universe_by_date.get(trade_date, len(values))
            observation_count = len(values)
            if observation_count == 0:
                # No evidence for this day: skip it rather than overwrite an
                # existing row with an empty cross-section.
                continue
            missing_count = max(universe_size - observation_count, 0)
            denominator = Decimal(universe_size) if universe_size else Decimal(1)
            mean = self._mean(values)
            standard_deviation = self._standard_deviation(values)
            extreme_count = 0
            if mean is not None and standard_deviation not in {None, Decimal()}:
                extreme_count = sum(
                    abs(value - mean) > standard_deviation * Decimal(3) for value in values
                )
            current_top_symbols = self._top_quantile_symbols(symbol_values)
            top_quantile_turnover = None
            if previous_top_symbols is not None and current_top_symbols:
                top_quantile_turnover = self._top_quantile_turnover(
                    previous_top_symbols,
                    current_top_symbols,
                )
            forward_returns_1d = forward_returns_by_horizon.get(1, {}).get(trade_date, {})
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
        factor_symbols = {symbol for values in values_by_date.values() for symbol in values}
        securities = self.security_repository.get_many(db, symbols=factor_symbols)
        exchange_by_symbol = {
            security.symbol: security.exchange for security in securities if security.exchange
        }
        exchanges = set(exchange_by_symbol.values())
        open_dates_by_exchange = {
            exchange: self.trading_calendar_repository.list_open_dates(
                db,
                exchange=exchange,
                start_date=min(target_dates),
                end_date=end_date + timedelta(days=FORWARD_CALENDAR_LOOKAHEAD_DAYS),
            )
            for exchange in exchanges
        }
        target_date_by_exchange: dict[str, dict[date, dict[int, date]]] = {}
        required_bar_dates = set(target_dates)
        for exchange, open_dates in open_dates_by_exchange.items():
            open_date_index = {trade_date: index for index, trade_date in enumerate(open_dates)}
            exchange_targets: dict[date, dict[int, date]] = {}
            for current_date in target_dates:
                current_index = open_date_index.get(current_date)
                if current_index is None:
                    continue
                horizon_targets = {}
                for horizon in FORWARD_RETURN_HORIZONS:
                    future_index = current_index + horizon
                    if future_index >= len(open_dates):
                        continue
                    future_date = open_dates[future_index]
                    horizon_targets[horizon] = future_date
                    required_bar_dates.add(future_date)
                exchange_targets[current_date] = horizon_targets
            target_date_by_exchange[exchange] = exchange_targets

        if not target_date_by_exchange:
            return {horizon: {} for horizon in FORWARD_RETURN_HORIZONS}
        bars = self.daily_bar_repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=max(required_bar_dates),
            adjust_type=adjust_type,
            symbols=sorted(factor_symbols) if factor_symbols else symbols,
            quality_status="trusted",
        )
        bars_by_symbol_date = {(bar.symbol, bar.trade_date): bar for bar in bars}

        returns_by_horizon: dict[int, dict[date, dict[str, Decimal]]] = {
            horizon: defaultdict(dict) for horizon in FORWARD_RETURN_HORIZONS
        }
        for current_date, symbol_values in values_by_date.items():
            for symbol in symbol_values:
                exchange = exchange_by_symbol.get(symbol)
                if exchange is None:
                    continue
                current_bar = bars_by_symbol_date.get((symbol, current_date))
                if current_bar is None:
                    continue
                horizon_targets = target_date_by_exchange.get(exchange, {}).get(current_date, {})
                for horizon, future_date in horizon_targets.items():
                    future_bar = bars_by_symbol_date.get((symbol, future_date))
                    if future_bar is None:
                        continue
                    forward_return = self._ratio_change(future_bar.close, current_bar.close)
                    if forward_return is not None:
                        returns_by_horizon[horizon][current_date][symbol] = forward_return
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
                    symbol: (value - mean) / standard_deviation for symbol, value in values.items()
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
                residuals.update({symbol: value - mean for symbol, value in group_values})
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
                    (x_demeaned[symbol] * y_demeaned[symbol] for symbol in common_values),
                    Decimal(),
                )
                / denominator
                if denominator
                else Decimal()
            )
            result[trade_date] = {
                symbol: y_demeaned[symbol] - beta * x_demeaned[symbol] for symbol in common_values
            }
        return result

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal | None:
        return rank_ic.mean(values)

    def _standard_deviation(self, values: list[Decimal]) -> Decimal | None:
        return rank_ic.standard_deviation(values)

    def _information_coefficient(
        self,
        factor_values: dict[str, Decimal],
        forward_returns: dict[str, Decimal],
    ) -> Decimal | None:
        return rank_ic.information_coefficient(factor_values, forward_returns)

    def _rank_information_coefficient(
        self,
        factor_values: dict[str, Decimal],
        forward_returns: dict[str, Decimal],
    ) -> Decimal | None:
        return rank_ic.rank_information_coefficient(factor_values, forward_returns)

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
            int((Decimal(len(ordered)) * Decimal("0.2")).to_integral_value(rounding=ROUND_CEILING)),
        )
        bottom_boundary, top_boundary = self._quantile_boundaries(
            [item[1] for item in ordered],
            quantile_count,
        )
        bottom_return = self._mean(
            [forward_return for _, value, forward_return in ordered if value <= bottom_boundary]
        )
        top_return = self._mean(
            [forward_return for _, value, forward_return in ordered if value >= top_boundary]
        )
        long_short_return = (
            None if top_return is None or bottom_return is None else top_return - bottom_return
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
        index = 0
        while index < count:
            end = index
            while end + 1 < count and ordered_values[end + 1][1] == ordered_values[index][1]:
                end += 1
            midpoint = Decimal(index + end) / Decimal(2)
            bucket_index = min(
                int(midpoint * Decimal(actual_bucket_count) / Decimal(count)),
                actual_bucket_count - 1,
            )
            buckets[bucket_index].extend(
                ordered_values[position][2] for position in range(index, end + 1)
            )
            index = end + 1
        populated_buckets = [
            (bucket_index, self._mean(bucket))
            for bucket_index, bucket in enumerate(buckets)
            if bucket
        ]
        if len(populated_buckets) < 2 or any(
            value is None for _, value in populated_buckets
        ):
            return None
        return self._correlation(
            [Decimal(bucket_index) for bucket_index, _ in populated_buckets],
            [value for _, value in populated_buckets if value is not None],
        )

    def _correlation(self, left: list[Decimal], right: list[Decimal]) -> Decimal | None:
        return rank_ic.correlation(left, right)

    @staticmethod
    def _rank_values(values: dict[str, Decimal]) -> dict[str, Decimal]:
        return rank_ic.rank_values(values)

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
        _, top_boundary = FactorCrossSectionService._quantile_boundaries(
            list(values.values()),
            count,
        )
        return {symbol for symbol, value in values.items() if value >= top_boundary}

    @staticmethod
    def _quantile_boundaries(
        values: list[Decimal],
        count: int,
    ) -> tuple[Decimal, Decimal]:
        """Return value boundaries; callers include every security tied there."""
        ordered = sorted(values)
        return ordered[count - 1], ordered[-count]

    @staticmethod
    def _top_quantile_turnover(
        previous: set[str],
        current: set[str],
    ) -> Decimal | None:
        """Symmetric set turnover for tie-inclusive top groups of varying size."""
        total_memberships = len(previous) + len(current)
        if total_memberships == 0:
            return None
        retained_twice = Decimal(2 * len(previous & current))
        return Decimal(1) - retained_twice / Decimal(total_memberships)

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
