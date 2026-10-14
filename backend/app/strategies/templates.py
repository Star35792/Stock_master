from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from app.backtest import TargetWeightSignal
from app.strategies.inputs import StrategyFactorFrame


def _require_finite_score(score: Decimal, context: str) -> None:
    if not score.is_finite():
        raise ValueError(
            f"{context} score must be finite; got {score}"
        )


class WeightingMode(StrEnum):
    """How a cross-section strategy converts scores into target weights."""

    EQUAL_WEIGHT_FULLY_INVESTED = "equal_weight_fully_invested"
    EQUAL_WEIGHT_PARTIAL = "equal_weight_partial"
    SCORE_WEIGHTED = "score_weighted"


class MinimumScorePolicy(StrEnum):
    """How much invested weight a strategy is allowed to leave as cash."""

    ALWAYS_FULLY_INVESTED = "always_fully_invested"
    HOLD_CASH_WHEN_NO_CANDIDATE = "hold_cash_when_no_candidate"


@dataclass(frozen=True)
class FactorScoreSpec:
    factor_name: str
    factor_version: str
    weight: Decimal = Decimal(1)
    higher_is_better: bool = True


@dataclass
class CrossSectionDecision:
    """One decision-date outcome that explains how a target portfolio was sized.

    Field semantics when ``decide_history`` aggregates multiple dates:

    - ``eligible_symbols``, ``scored_symbols``, ``selected_symbols``,
      ``dropped_for_coverage`` and ``below_minimum_score_count`` are per-date
      counters summed across all decision dates.
    - ``distinct_selected_symbols`` is a set-semantic count of unique symbols
      ever selected with a positive target weight across the whole period.
    - ``invested_weight``, ``coverage_threshold``, ``weighting_mode``,
      ``minimum_score_policy`` and ``signal_date`` always reflect the **latest**
      decision date.

    For a single ``decide`` call the per-date counters and the latest-date
    values describe the same day.
    """


    signal_date: date
    signals: list[TargetWeightSignal] = field(default_factory=list)
    eligible_symbols: int = 0
    scored_symbols: int = 0
    selected_symbols: int = 0
    distinct_selected_symbols: int = 0
    dropped_for_coverage: int = 0
    invested_weight: Decimal = Decimal()
    coverage_threshold: Decimal = Decimal(1)
    weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
    minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED
    below_minimum_score_count: int = 0


class CrossSectionSelectionStrategy:
    def __init__(
        self,
        *,
        top_n: int,
        allowed_security_types: set[str],
        coverage_ratio: Decimal | None = None,
        weighting_mode: WeightingMode = WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED,
        minimum_score_policy: MinimumScorePolicy = MinimumScorePolicy.ALWAYS_FULLY_INVESTED,
        minimum_score: Decimal | None = None,
        invested_weight: Decimal = Decimal(1),
    ) -> None:
        if top_n <= 0:
            raise ValueError("top_n must be positive")
        if coverage_ratio is not None and not Decimal(0) < coverage_ratio <= Decimal(1):
            raise ValueError("coverage_ratio must be within (0, 1]")
        if not Decimal(0) < invested_weight <= Decimal(1):
            raise ValueError("invested_weight must be within (0, 1]")
        if (
            minimum_score_policy is MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE
            and weighting_mode is WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
            and invested_weight != Decimal(1)
        ):
            raise ValueError(
                f"{MinimumScorePolicy.HOLD_CASH_WHEN_NO_CANDIDATE} with "
                f"{WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED} always invests 100%; "
                "choose another weighting mode to leave cash"
            )
        if minimum_score is not None and minimum_score < 0:
            raise ValueError(
                "minimum_score must be non-negative; a negative threshold would "
                "admit symbols whose score itself is negative"
            )
        self.top_n = top_n
        self.allowed_security_types = allowed_security_types
        self.coverage_ratio = coverage_ratio
        self.weighting_mode = weighting_mode
        self.minimum_score_policy = minimum_score_policy
        self.minimum_score = minimum_score
        self.invested_weight = invested_weight

    def generate(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
        previous_symbols: set[str] | None = None,
    ) -> list[TargetWeightSignal]:
        return self.decide(
            frame=frame,
            security_types=security_types,
            previous_symbols=previous_symbols,
        ).signals

    def decide(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
        previous_symbols: set[str] | None = None,
    ) -> CrossSectionDecision:
        frame.require_exact_as_of()
        factor_rows = frame.rows_by_factor
        scores_by_date: dict[date, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        coverage_by_date: dict[date, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        eligible_by_date: dict[date, set[str]] = defaultdict(set)
        total_weight = sum((abs(spec.weight) for spec in factor_rows), Decimal())
        if total_weight == 0:
            raise ValueError("factor weights must not all be zero")
        factor_count = len(factor_rows)
        coverage_threshold = (
            self.coverage_ratio if self.coverage_ratio is not None else Decimal(1)
        )

        for spec, rows in factor_rows.items():
            rows_by_date: dict[date, list[Any]] = defaultdict(list)
            for row in rows:
                if security_types.get(row.symbol) in self.allowed_security_types:
                    rows_by_date[row.trade_date].append(row)
                    eligible_by_date[row.trade_date].add(row.symbol)
            for trade_date, daily_rows in rows_by_date.items():
                ranked = self._percentile_ranks(
                    {row.symbol: row.value for row in daily_rows},
                    higher_is_better=spec.higher_is_better,
                )
                for symbol, rank in ranked.items():
                    scores_by_date[trade_date][symbol] += rank * spec.weight
                    coverage_by_date[trade_date][symbol] += 1

        signals: list[TargetWeightSignal] = []
        prior_selection = set(previous_symbols or set())
        decisions: list[CrossSectionDecision] = []
        for signal_date in sorted(scores_by_date):
            daily = self._decide_date(
                signal_date=signal_date,
                scores=scores_by_date[signal_date],
                coverage=coverage_by_date[signal_date],
                eligible_symbols=eligible_by_date[signal_date],
                prior_selection=prior_selection,
                total_weight=total_weight,
                factor_count=factor_count,
                coverage_threshold=coverage_threshold,
            )
            decisions.append(daily)
            signals.extend(daily.signals)
            prior_selection = {
                signal.symbol for signal in daily.signals if signal.target_weight > 0
            }
        return self._aggregate(decisions, frame)

    def generate_history(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
    ) -> list[TargetWeightSignal]:
        return self.decide_history(
            frame=frame, security_types=security_types
        ).signals

    def decide_history(
        self,
        *,
        frame: StrategyFactorFrame,
        security_types: dict[str, str],
    ) -> CrossSectionDecision:
        decisions: list[CrossSectionDecision] = []
        previous_symbols: set[str] = set()
        for as_of_date in frame.observation_dates:
            daily = self.decide(
                frame=frame.for_date(as_of_date),
                security_types=security_types,
                previous_symbols=previous_symbols,
            )
            decisions.append(daily)
            previous_symbols = {
                signal.symbol
                for signal in daily.signals
                if signal.target_weight > 0
            }
        return self._aggregate(decisions, frame)

    def _decide_date(
        self,
        *,
        signal_date: date,
        scores: dict[str, Decimal],
        coverage: dict[str, int],
        eligible_symbols: set[str],
        prior_selection: set[str],
        total_weight: Decimal,
        factor_count: int,
        coverage_threshold: Decimal,
    ) -> CrossSectionDecision:
        covered: dict[str, Decimal] = {}
        for symbol, score in scores.items():
            covered_count = coverage.get(symbol, 0)
            if not self._meets_coverage(
                covered_count=covered_count,
                factor_count=factor_count,
                coverage_threshold=coverage_threshold,
            ):
                continue
            normalized = score / total_weight
            _require_finite_score(normalized, f"normalized score for {symbol!r}")
            covered[symbol] = normalized
        below_minimum_score_count = 0
        if self.minimum_score is not None:
            filtered: dict[str, Decimal] = {}
            for symbol, score in covered.items():
                if score >= self.minimum_score:
                    filtered[symbol] = score
                else:
                    below_minimum_score_count += 1
            covered = filtered
        dropped_for_coverage = len(eligible_symbols) - len(covered) - below_minimum_score_count
        if not covered:
            return CrossSectionDecision(
                signal_date=signal_date,
                signals=[
                    TargetWeightSignal(signal_date, symbol, Decimal())
                    for symbol in sorted(prior_selection)
                ],
                eligible_symbols=len(eligible_symbols),
                scored_symbols=0,
                selected_symbols=0,
                distinct_selected_symbols=0,
                dropped_for_coverage=dropped_for_coverage,
                invested_weight=Decimal(),
                coverage_threshold=coverage_threshold,
                weighting_mode=self.weighting_mode,
                minimum_score_policy=self.minimum_score_policy,
                below_minimum_score_count=below_minimum_score_count,
            )

        selected_symbols = [
            symbol
            for symbol, _ in sorted(
                covered.items(), key=lambda item: (-item[1], item[0])
            )[: self.top_n]
        ]

        weights = self._target_weights(covered, selected_symbols)
        selected_set = set(selected_symbols)
        signals = [
            TargetWeightSignal(signal_date, symbol, Decimal())
            for symbol in sorted(prior_selection - selected_set)
        ]
        signals.extend(
            TargetWeightSignal(signal_date, symbol, weights[symbol])
            for symbol in sorted(selected_symbols)
        )
        return CrossSectionDecision(
            signal_date=signal_date,
            signals=signals,
            eligible_symbols=len(eligible_symbols),
            scored_symbols=len(covered),
            selected_symbols=len(selected_symbols),
            distinct_selected_symbols=len(selected_symbols),
            dropped_for_coverage=dropped_for_coverage,
            invested_weight=sum(weights.values(), Decimal()),
            coverage_threshold=coverage_threshold,
            weighting_mode=self.weighting_mode,
            minimum_score_policy=self.minimum_score_policy,
            below_minimum_score_count=below_minimum_score_count,
        )

    def _target_weights(
        self, scores: dict[str, Decimal], selected: list[str]
    ) -> dict[str, Decimal]:
        if not selected:
            return {}
        if self.weighting_mode is WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED:
            budget = Decimal(1)
        else:
            budget = self.invested_weight
        if self.weighting_mode is WeightingMode.SCORE_WEIGHTED:
            for symbol in selected:
                _require_finite_score(
                    scores[symbol], f"score_weighted symbol {symbol!r}"
                )
            minimum = min(scores[symbol] for symbol in selected)
            if minimum < 0:
                raise ValueError(
                    "score_weighted mode cannot size a negative-score symbol: "
                    "a short position is not executable for A-share stocks; "
                    "exclude it with a higher top_n cutoff or use "
                    f"{WeightingMode.EQUAL_WEIGHT_PARTIAL} instead"
                )
            total = sum((scores[symbol] for symbol in selected), Decimal())
            if total <= 0:
                raise ValueError(
                    "score_weighted mode requires a positive summed score; "
                    f"use {WeightingMode.EQUAL_WEIGHT_PARTIAL} instead"
                )
            return {symbol: budget * scores[symbol] / total for symbol in selected}
        per_name = budget / Decimal(len(selected))
        residual = budget - per_name * Decimal(len(selected) - 1)
        return {
            symbol: (residual if index == 0 else per_name)
            for index, symbol in enumerate(selected)
        }

    def _meets_coverage(
        self,
        *,
        covered_count: int,
        factor_count: int,
        coverage_threshold: Decimal,
    ) -> bool:
        if covered_count <= 0:
            return False
        if factor_count <= 0:
            return False
        required = Decimal(factor_count) * coverage_threshold
        return Decimal(covered_count) >= required

    @staticmethod
    def _aggregate(
        decisions: list[CrossSectionDecision], frame: StrategyFactorFrame
    ) -> CrossSectionDecision:
        signals: list[TargetWeightSignal] = []
        for decision in decisions:
            signals.extend(decision.signals)
        last = decisions[-1] if decisions else None
        return CrossSectionDecision(
            signal_date=last.signal_date if last else frame.as_of_date,
            signals=signals,
            eligible_symbols=sum(d.eligible_symbols for d in decisions),
            scored_symbols=sum(d.scored_symbols for d in decisions),
            selected_symbols=sum(d.selected_symbols for d in decisions),
            distinct_selected_symbols=len(
                {s.symbol for s in signals if s.target_weight > 0}
            ),
            dropped_for_coverage=sum(d.dropped_for_coverage for d in decisions),
            invested_weight=last.invested_weight if last else Decimal(),
            coverage_threshold=last.coverage_threshold if last else Decimal(1),
            weighting_mode=(
                last.weighting_mode
                if last
                else WeightingMode.EQUAL_WEIGHT_FULLY_INVESTED
            ),
            minimum_score_policy=(
                last.minimum_score_policy
                if last
                else MinimumScorePolicy.ALWAYS_FULLY_INVESTED
            ),
            below_minimum_score_count=sum(d.below_minimum_score_count for d in decisions),
        )

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
    def __init__(self, *, top_n: int, **sizing: Any) -> None:
        super().__init__(top_n=top_n, allowed_security_types={"stock"}, **sizing)


class EtfRotationStrategy(CrossSectionSelectionStrategy):
    def __init__(self, *, top_n: int = 1, **sizing: Any) -> None:
        super().__init__(top_n=top_n, allowed_security_types={"etf"}, **sizing)


class MultiFactorStrategy(CrossSectionSelectionStrategy):
    def __init__(
        self,
        *,
        top_n: int,
        allowed_security_types: set[str] | None = None,
        **sizing: Any,
    ) -> None:
        super().__init__(
            top_n=top_n,
            allowed_security_types=allowed_security_types or {"stock", "etf", "index"},
            **sizing,
        )
