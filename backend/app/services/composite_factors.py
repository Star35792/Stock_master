from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import SecurityRepository
from app.strategies import FactorScoreSpec

FACTOR_NAME = "decision_score"
FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
DEFAULT_SECURITY_TYPES = ("stock", "etf", "index")


@dataclass(frozen=True)
class CompositeFactorRunResult:
    factor_name: str
    factor_version: str
    start_date: date
    end_date: date
    input_rows: int
    rows_written: int
    run_id: int


class CompositeFactorService:
    def __init__(
        self,
        value_repository: FactorValueRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        security_repository: SecurityRepository | None = None,
    ) -> None:
        self.value_repository = value_repository or FactorValueRepository()
        self.definition_repository = definition_repository or FactorDefinitionRepository()
        self.run_repository = run_repository or FactorRunRepository()
        self.security_repository = security_repository or SecurityRepository()

    def run(
        self,
        db: Session,
        *,
        factor_specs: list[FactorScoreSpec],
        start_date: date,
        end_date: date,
        factor_name: str = FACTOR_NAME,
        factor_version: str = FACTOR_VERSION,
        display_name: str = "Decision score",
        description: str = "weighted percentile-rank composite decision score",
        symbols: list[str] | None = None,
        allowed_security_types: set[str] | None = None,
        code_version: str = "local",
    ) -> CompositeFactorRunResult:
        self._validate_inputs(
            factor_specs=factor_specs,
            start_date=start_date,
            end_date=end_date,
        )
        normalized_symbols = (
            sorted({symbol.strip().upper() for symbol in symbols if symbol.strip()})
            if symbols
            else None
        )
        security_types_allowed = sorted(
            allowed_security_types or set(DEFAULT_SECURITY_TYPES)
        )

        rows_by_spec: dict[FactorScoreSpec, list[Any]] = {}
        input_rows = 0
        all_symbols: set[str] = set()
        for spec in factor_specs:
            rows = self.value_repository.list_for_cross_section(
                db,
                factor_name=spec.factor_name,
                factor_version=spec.factor_version,
                start_date=start_date,
                end_date=end_date,
            )
            if normalized_symbols is not None:
                symbol_set = set(normalized_symbols)
                rows = [row for row in rows if row.symbol in symbol_set]
            rows_by_spec[spec] = rows
            input_rows += len(rows)
            all_symbols.update(row.symbol for row in rows)

        security_types = self.security_repository.get_security_types(db, all_symbols)
        component_inputs = [
            f"factor.{spec.factor_name}:{spec.factor_version}"
            for spec in factor_specs
        ]
        definition = self.definition_repository.ensure(
            db,
            name=factor_name,
            display_name=display_name,
            description=description,
            category=FactorCategory.COMPOSITE,
            frequency="daily",
            inputs=component_inputs,
            applicable_security_types=security_types_allowed,
            window_days=None,
            version=factor_version,
            dependencies=component_inputs,
            parameters_schema={
                "components": [
                    {
                        "factor_name": spec.factor_name,
                        "factor_version": spec.factor_version,
                        "weight": str(spec.weight),
                        "higher_is_better": spec.higher_is_better,
                    }
                    for spec in factor_specs
                ]
            },
        )
        run = self.run_repository.create(
            db,
            definition=definition,
            parameters={
                "target_start_date": start_date.isoformat(),
                "target_end_date": end_date.isoformat(),
                "symbols": normalized_symbols,
                "allowed_security_types": security_types_allowed,
                "scoring_policy": "weighted_percentile_rank",
                "coverage_policy": "complete_components",
            },
            code_version=code_version,
            input_start_date=start_date,
            input_end_date=end_date,
            metadata={
                "components": [
                    {
                        "factor_name": spec.factor_name,
                        "factor_version": spec.factor_version,
                        "weight": str(spec.weight),
                        "higher_is_better": spec.higher_is_better,
                    }
                    for spec in factor_specs
                ],
                "score_policy": "weighted_component_percentiles",
            },
        )
        rows_written = self._write_values(
            db,
            factor_specs=factor_specs,
            rows_by_spec=rows_by_spec,
            security_types=security_types,
            allowed_security_types=set(security_types_allowed),
            factor_name=factor_name,
            factor_version=factor_version,
            run_id=run.id,
        )
        self.run_repository.update_status(
            db,
            run_id=run.id,
            status="success",
            rows_written=rows_written,
        )
        return CompositeFactorRunResult(
            factor_name=factor_name,
            factor_version=factor_version,
            start_date=start_date,
            end_date=end_date,
            input_rows=input_rows,
            rows_written=rows_written,
            run_id=run.id,
        )

    def _write_values(
        self,
        db: Session,
        *,
        factor_specs: list[FactorScoreSpec],
        rows_by_spec: dict[FactorScoreSpec, list[Any]],
        security_types: dict[str, str],
        allowed_security_types: set[str],
        factor_name: str,
        factor_version: str,
        run_id: int,
    ) -> int:
        scores_by_date: dict[date, dict[str, Decimal]] = defaultdict(
            lambda: defaultdict(Decimal)
        )
        coverage_by_date: dict[date, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        total_weight = sum((abs(spec.weight) for spec in factor_specs), Decimal())

        for spec, rows in rows_by_spec.items():
            rows_by_date: dict[date, list[Any]] = defaultdict(list)
            for row in rows:
                if security_types.get(row.symbol) in allowed_security_types:
                    rows_by_date[row.trade_date].append(row)
            for trade_date, daily_rows in rows_by_date.items():
                ranks = _percentile_ranks(
                    {row.symbol: row.value for row in daily_rows},
                    higher_is_better=spec.higher_is_better,
                )
                for symbol, rank in ranks.items():
                    scores_by_date[trade_date][symbol] += rank * spec.weight
                    coverage_by_date[trade_date][symbol] += 1

        rows_written = 0
        factor_count = len(factor_specs)
        for trade_date in sorted(scores_by_date):
            for symbol in sorted(scores_by_date[trade_date]):
                if coverage_by_date[trade_date][symbol] != factor_count:
                    continue
                value = scores_by_date[trade_date][symbol] / total_weight
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

    @staticmethod
    def _validate_inputs(
        *,
        factor_specs: list[FactorScoreSpec],
        start_date: date,
        end_date: date,
    ) -> None:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if not factor_specs:
            raise ValueError("factor_specs must not be empty")
        keys = [
            (spec.factor_name, spec.factor_version)
            for spec in factor_specs
        ]
        duplicates = {key for key in keys if keys.count(key) > 1}
        if duplicates:
            names = ", ".join(f"{name}:{version}" for name, version in sorted(duplicates))
            raise ValueError(f"duplicate factor specs: {names}")
        total_weight = sum((abs(spec.weight) for spec in factor_specs), Decimal())
        if total_weight == 0:
            raise ValueError("factor weights must not all be zero")


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
