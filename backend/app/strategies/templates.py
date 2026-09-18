from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.backtest import TargetWeightSignal
from app.strategies.inputs import StrategyFactorFrame


@dataclass(frozen=True)
class FactorScoreSpec:
    factor_name: str
    factor_version: str
    weight: Decimal = Decimal(1)
    higher_is_better: bool = True


class CrossSectionSelectionStrategy:
    def __init__(self, *, top_n: int, allowed_security_types: set[str]) -> None:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        self.top_n = top_n
        self.allowed_security_types = allowed_security_types

    def generate(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
        previous_symbols: set[str] | None = None,
    ) -> list[TargetWeightSignal]:
        frame.require_exact_as_of()
        factor_rows = frame.rows_by_factor
        scores_by_date: dict[date, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        coverage_by_date: dict[date, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        total_weight = sum((abs(spec.weight) for spec in factor_rows), Decimal())
        if total_weight == 0:
            raise ValueError("factor weights must not all be zero")

        for spec, rows in factor_rows.items():
            rows_by_date: dict[date, list[Any]] = defaultdict(list)
            for row in rows:
                if security_types.get(row.symbol) in self.allowed_security_types:
                    rows_by_date[row.trade_date].append(row)
            for trade_date, daily_rows in rows_by_date.items():
                ranked = self._percentile_ranks(
                    {row.symbol: row.value for row in daily_rows},
                    higher_is_better=spec.higher_is_better,
                )
                for symbol, rank in ranked.items():
                    scores_by_date[trade_date][symbol] += rank * spec.weight
                    coverage_by_date[trade_date][symbol] += 1

        signals: list[TargetWeightSignal] = []
        factor_count = len(factor_rows)
        prior_selection = set(previous_symbols or set())
        for signal_date in sorted(scores_by_date):
            complete_scores = {
                symbol: score / total_weight
                for symbol, score in scores_by_date[signal_date].items()
                if coverage_by_date[signal_date][symbol] == factor_count
            }
            selected = {
                symbol
                for symbol, _ in sorted(
                    complete_scores.items(),
                    key=lambda item: (-item[1], item[0]),
                )[: self.top_n]
            }
            weight = Decimal(1) / Decimal(len(selected)) if selected else Decimal()
            for symbol in sorted(prior_selection - selected):
                signals.append(TargetWeightSignal(signal_date, symbol, Decimal()))
            for symbol in sorted(selected):
                signals.append(TargetWeightSignal(signal_date, symbol, weight))
        return signals

    def generate_history(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
    ) -> list[TargetWeightSignal]:
        signals: list[TargetWeightSignal] = []
        previous_symbols: set[str] = set()
        for as_of_date in frame.observation_dates:
            daily_signals = self.generate(
                frame=frame.for_date(as_of_date),
                security_types=security_types,
                previous_symbols=previous_symbols,
            )
            signals.extend(daily_signals)
            previous_symbols = {
                signal.symbol
                for signal in daily_signals
                if signal.target_weight > 0
            }
        return signals

    @staticmethod
    def _percentile_ranks(
        values: dict[str, Decimal],
        *,
        higher_is_better: bool,
    ) -> dict[str, Decimal]:
        ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
        if len(ordered) == 1:
            return {ordered[0][0]: Decimal(1)}
        return {
            symbol: (
                Decimal(index) / Decimal(len(ordered) - 1)
                if higher_is_better
                else Decimal(len(ordered) - index - 1) / Decimal(len(ordered) - 1)
            )
            for index, (symbol, _) in enumerate(ordered)
        }


class IndexTimingStrategy:
    def __init__(self, *, threshold: Decimal, invested_weight: Decimal = Decimal(1)) -> None:
        if invested_weight < 0 or invested_weight > 1:
            raise ValueError("invested_weight must be between 0 and 1")
        self.threshold = threshold
        self.invested_weight = invested_weight

    def generate(
        self,
        *,
        frame: StrategyFactorFrame,
        symbol: str,
    ) -> list[TargetWeightSignal]:
        frame.require_exact_as_of()
        if len(frame.rows_by_factor) != 1:
            raise ValueError("index timing strategy requires exactly one factor input")
        rows = next(iter(frame.rows_by_factor.values()))
        return [
            TargetWeightSignal(
                signal_date=row.trade_date,
                symbol=symbol,
                target_weight=(
                    self.invested_weight if row.value >= self.threshold else Decimal()
                ),
            )
            for row in rows
            if row.symbol == symbol
        ]

    def generate_history(
        self,
        *,
        frame: StrategyFactorFrame,
        symbol: str,
    ) -> list[TargetWeightSignal]:
        return [
            signal
            for as_of_date in frame.observation_dates
            for signal in self.generate(frame=frame.for_date(as_of_date), symbol=symbol)
        ]


class StockPoolStrategy(CrossSectionSelectionStrategy):
    def __init__(self, *, top_n: int) -> None:
        super().__init__(top_n=top_n, allowed_security_types={"stock"})


class EtfRotationStrategy(CrossSectionSelectionStrategy):
    def __init__(self, *, top_n: int = 1) -> None:
        super().__init__(top_n=top_n, allowed_security_types={"etf"})


class MultiFactorStrategy(CrossSectionSelectionStrategy):
    def __init__(self, *, top_n: int, allowed_security_types: set[str] | None = None) -> None:
        super().__init__(
            top_n=top_n,
            allowed_security_types=allowed_security_types or {"stock", "etf", "index"},
        )
