from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.factors.rank_ic import rank_values
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import DailyBarRepository

FACTOR_NAME = "crowding_score"
FACTOR_VERSION = "v2-average-rank"
COMPONENT_FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock",)
LOOKBACK_DAYS = 60
SENTIMENT_HISTORY_DAYS = 20

TURNOVER_FACTOR_NAME = "turnover_percentile_120"
ABNORMAL_VOLUME_FACTOR_NAME = "abnormal_volume_pressure"
SENTIMENT_FACTOR_NAME = "market_sentiment_score"


@dataclass(frozen=True)
class CrowdingFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


class CrowdingFactorService:
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
        adjust_type: str = "qfq",
        code_version: str = "local",
        release_id: str | None = None,
    ) -> CrowdingFactorRunResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")

        factor_version = f"{FACTOR_VERSION}-{adjust_type}"
        component_factor_version = f"{COMPONENT_FACTOR_VERSION}-{adjust_type}"
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

        target_symbols = set(bars_by_symbol)
        turnover_rows = self.value_repository.list_for_cross_section(
            db,
            factor_name=TURNOVER_FACTOR_NAME,
            factor_version=component_factor_version,
            start_date=input_start_date,
            end_date=end_date,
        )
        abnormal_rows = self.value_repository.list_for_cross_section(
            db,
            factor_name=ABNORMAL_VOLUME_FACTOR_NAME,
            factor_version=component_factor_version,
            start_date=input_start_date,
            end_date=end_date,
        )
        sentiment_rows = self.value_repository.list_for_cross_section(
            db,
            factor_name=SENTIMENT_FACTOR_NAME,
            factor_version=COMPONENT_FACTOR_VERSION,
            start_date=input_start_date,
            end_date=end_date,
        )

        turnover_by_date = _group_symbol_values(
            turnover_rows,
            target_symbols=target_symbols,
        )
        abnormal_by_date = _group_symbol_values(
            abnormal_rows,
            target_symbols=target_symbols,
        )
        sentiment_by_date = _group_market_values(sentiment_rows)
        sentiment_rank_by_date = _rolling_percentile_by_date(
            sentiment_by_date,
            history_days=SENTIMENT_HISTORY_DAYS,
        )

        component_inputs = [
            f"factor.{TURNOVER_FACTOR_NAME}:{component_factor_version}",
            f"factor.{ABNORMAL_VOLUME_FACTOR_NAME}:{component_factor_version}",
            f"factor.{SENTIMENT_FACTOR_NAME}:{COMPONENT_FACTOR_VERSION}",
        ]
        definition = self.definition_repository.ensure(
            db,
            name=FACTOR_NAME,
            display_name="Crowding score",
            description=(
                "equal-weight blend of turnover percentile, abnormal volume "
                "pressure and market sentiment regime"
            ),
            category=FactorCategory.CAPITAL_FLOW,
            frequency="daily",
            inputs=component_inputs,
            applicable_security_types=list(SUPPORTED_SECURITY_TYPES),
            window_days=None,
            version=factor_version,
            dependencies=component_inputs,
            parameters_schema={
                "components": [
                    {
                        "factor_name": TURNOVER_FACTOR_NAME,
                        "factor_version": component_factor_version,
                        "weight": "1",
                    },
                    {
                        "factor_name": ABNORMAL_VOLUME_FACTOR_NAME,
                        "factor_version": component_factor_version,
                        "weight": "1",
                    },
                    {
                        "factor_name": SENTIMENT_FACTOR_NAME,
                        "factor_version": COMPONENT_FACTOR_VERSION,
                        "weight": "1",
                    },
                ],
                "sentiment_history_days": SENTIMENT_HISTORY_DAYS,
                "cross_section_rank_method": "average_rank",
            },
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
                "lookback_days": LOOKBACK_DAYS,
                "sentiment_history_days": SENTIMENT_HISTORY_DAYS,
                "component_versions": {
                    TURNOVER_FACTOR_NAME: component_factor_version,
                    ABNORMAL_VOLUME_FACTOR_NAME: component_factor_version,
                    SENTIMENT_FACTOR_NAME: COMPONENT_FACTOR_VERSION,
                },
                "cross_section_rank_method": "average_rank",
            },
            code_version=code_version,
            release_id=release_id,
            input_start_date=input_start_date,
            input_end_date=end_date,
            metadata={
                "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                "components": component_inputs,
                "sentiment_history_days": SENTIMENT_HISTORY_DAYS,
                "cross_section_rank_method": "average_rank",
            },
        )
        rows_written = self._write_factor_values(
            db,
            bars_by_symbol=bars_by_symbol,
            turnover_by_date=turnover_by_date,
            abnormal_by_date=abnormal_by_date,
            sentiment_rank_by_date=sentiment_rank_by_date,
            start_date=start_date,
            end_date=end_date,
            factor_version=factor_version,
            run_id=run.id,
        )
        self.run_repository.update_status(
            db,
            run_id=run.id,
            status="success",
            rows_written=rows_written,
        )
        return CrowdingFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=(
                len(bars)
                + len(turnover_rows)
                + len(abnormal_rows)
                + len(sentiment_rows)
            ),
            rows_by_factor={FACTOR_NAME: rows_written},
            run_ids={FACTOR_NAME: run.id},
        )

    def _write_factor_values(
        self,
        db: Session,
        *,
        bars_by_symbol: dict[str, list[Any]],
        turnover_by_date: dict[date, dict[str, Decimal]],
        abnormal_by_date: dict[date, dict[str, Decimal]],
        sentiment_rank_by_date: dict[date, Decimal],
        start_date: date,
        end_date: date,
        factor_version: str,
        run_id: int,
    ) -> int:
        rows_written = 0
        turnover_ranks = _percentile_ranks_by_date(turnover_by_date)
        abnormal_ranks = _percentile_ranks_by_date(abnormal_by_date)
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                if bar.trade_date < start_date or bar.trade_date > end_date:
                    continue
                turnover_rank = turnover_ranks.get(bar.trade_date, {}).get(symbol)
                abnormal_rank = abnormal_ranks.get(bar.trade_date, {}).get(symbol)
                sentiment_rank = sentiment_rank_by_date.get(bar.trade_date)
                if (
                    turnover_rank is None
                    or abnormal_rank is None
                    or sentiment_rank is None
                ):
                    continue
                value = (turnover_rank + abnormal_rank + sentiment_rank) / Decimal(3)
                self.value_repository.upsert_value(
                    db,
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    factor_name=FACTOR_NAME,
                    factor_version=factor_version,
                    value=value.quantize(VALUE_QUANT),
                    run_id=run_id,
                )
                rows_written += 1
        return rows_written


def _group_symbol_values(
    rows: list[Any],
    *,
    target_symbols: set[str],
) -> dict[date, dict[str, Decimal]]:
    grouped: dict[date, dict[str, Decimal]] = defaultdict(dict)
    for row in rows:
        if row.symbol in target_symbols and row.value is not None:
            grouped[row.trade_date][row.symbol] = row.value
    return {trade_date: dict(values) for trade_date, values in grouped.items()}


def _group_market_values(rows: list[Any]) -> dict[date, Decimal]:
    grouped: dict[date, Decimal] = {}
    for row in rows:
        if row.value is not None and row.trade_date not in grouped:
            grouped[row.trade_date] = row.value
    return grouped


def _percentile_ranks_by_date(
    values_by_date: dict[date, dict[str, Decimal]],
) -> dict[date, dict[str, Decimal]]:
    return {
        trade_date: _percentile_ranks(values, higher_is_better=True)
        for trade_date, values in values_by_date.items()
    }


def _rolling_percentile_by_date(
    values_by_date: dict[date, Decimal],
    *,
    history_days: int,
) -> dict[date, Decimal]:
    result: dict[date, Decimal] = {}
    history: list[Decimal] = []
    for trade_date in sorted(values_by_date):
        current = values_by_date[trade_date]
        window = history[-(history_days - 1) :] if history_days > 1 else []
        window = window + [current]
        percentile = _percentile_rank(window, current)
        if percentile is not None:
            result[trade_date] = percentile
        history.append(current)
    return result


def _percentile_ranks(
    values: dict[str, Decimal],
    *,
    higher_is_better: bool,
) -> dict[str, Decimal]:
    ranks = rank_values(values)
    if higher_is_better:
        return ranks
    return {symbol: Decimal(1) - rank for symbol, rank in ranks.items()}


def _percentile_rank(values: list[Decimal], current: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return Decimal(1)
    less = sum(value < current for value in ordered)
    equal = sum(value == current for value in ordered)
    return (
        Decimal(less) + (Decimal(equal) - Decimal(1)) / Decimal(2)
    ) / Decimal(len(ordered) - 1)
