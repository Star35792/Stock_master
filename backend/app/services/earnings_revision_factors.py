from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.factors.categories import FactorCategory
from app.repositories.factor import (
    FactorDefinitionRepository,
    FactorRunRepository,
    FactorValueRepository,
)
from app.repositories.market import (
    AlternativeDataSnapshotRepository,
    DailyBarRepository,
    SecurityRepository,
)

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock",)
LOOKBACK_DAYS = 45

REVISION_DATASET = "earnings_revision"
GUIDANCE_DATASET = "profit_guidance"
WARNING_DATASET = "profit_warning"


@dataclass(frozen=True)
class EarningsRevisionFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    datasets: tuple[str, ...]
    market_wide: bool = False
    higher_is_better: bool = True
    category: str = FactorCategory.EARNINGS_REVISION


@dataclass(frozen=True)
class EarningsRevisionFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


EARNINGS_REVISION_FACTOR_SPECS = (
    EarningsRevisionFactorSpec(
        name="earnings_revision_1m",
        display_name="Earnings revision 1M",
        description="latest one-month consensus earnings revision",
        inputs=("market.alternative_data_snapshot.earnings_revision.value",),
        datasets=(REVISION_DATASET,),
    ),
    EarningsRevisionFactorSpec(
        name="earnings_revision_breadth",
        display_name="Earnings revision breadth",
        description="industry share of upward revisions minus share of downward revisions",
        inputs=(
            "market.alternative_data_snapshot.earnings_revision.value",
            "market.security.industry",
        ),
        datasets=(REVISION_DATASET,),
        market_wide=True,
    ),
    EarningsRevisionFactorSpec(
        name="forecast_dispersion",
        display_name="Forecast dispersion",
        description="latest analyst forecast dispersion proxy",
        inputs=("market.alternative_data_snapshot.earnings_revision.attributes",),
        datasets=(REVISION_DATASET,),
        higher_is_better=False,
    ),
    EarningsRevisionFactorSpec(
        name="guidance_surprise",
        display_name="Guidance surprise",
        description="latest profit guidance surprise versus market expectation",
        inputs=("market.alternative_data_snapshot.profit_guidance.value",),
        datasets=(GUIDANCE_DATASET,),
    ),
    EarningsRevisionFactorSpec(
        name="profit_warning_risk",
        display_name="Profit warning risk",
        description="latest profit-warning risk proxy",
        inputs=("market.alternative_data_snapshot.profit_warning.value",),
        datasets=(WARNING_DATASET,),
        higher_is_better=False,
    ),
)


class EarningsRevisionFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        snapshot_repository: AlternativeDataSnapshotRepository | None = None,
        security_repository: SecurityRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.snapshot_repository = snapshot_repository or AlternativeDataSnapshotRepository()
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
        release_id: str | None = None,
    ) -> EarningsRevisionFactorRunResult:
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
        bars = self.daily_bar_repository.list_for_factor_calculation(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
            quality_status="trusted",
        )
        bars_by_symbol: dict[str, list[Any]] = defaultdict(list)
        for bar in bars:
            if bar.security_type in SUPPORTED_SECURITY_TYPES:
                bars_by_symbol[bar.symbol].append(bar)

        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
        datasets = sorted({dataset for spec in specs for dataset in spec.datasets})
        snapshots = self.snapshot_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            datasets=datasets,
        )
        snapshots_by_dataset_symbol = _group_symbol_snapshots(snapshots)
        industries = self.security_repository.get_industries(db, set(bars_by_symbol))

        rows_by_factor: dict[str, int] = {}
        run_ids: dict[str, int] = {}
        for spec in specs:
            definition = self.definition_repository.ensure(
                db,
                name=spec.name,
                display_name=spec.display_name,
                description=spec.description,
                category=spec.category,
                frequency="daily",
                inputs=list(spec.inputs),
                applicable_security_types=list(SUPPORTED_SECURITY_TYPES),
                window_days=LOOKBACK_DAYS,
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
                    "availability_policy": "snapshot_date_before_trade_date",
                    "datasets": list(spec.datasets),
                    "lookback_days": LOOKBACK_DAYS,
                },
                code_version=code_version,
                release_id=release_id,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_revision_snapshot_or_industry",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                    "market_wide": spec.market_wide,
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                snapshots_by_dataset_symbol=snapshots_by_dataset_symbol,
                industries=industries,
                run_id=run.id,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_factor[spec.name] = rows_written
            run_ids[spec.name] = run.id

        return EarningsRevisionFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(snapshots),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(
        self,
        factor_names: list[str] | None,
    ) -> tuple[EarningsRevisionFactorSpec, ...]:
        if not factor_names:
            return EARNINGS_REVISION_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return EARNINGS_REVISION_FACTOR_SPECS
        available = {spec.name for spec in EARNINGS_REVISION_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(
            spec for spec in EARNINGS_REVISION_FACTOR_SPECS if spec.name in requested
        )

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: EarningsRevisionFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        industries: dict[str, str],
        run_id: int,
    ) -> int:
        rows_written = 0
        for symbol, bars in bars_by_symbol.items():
            industry = industries.get(symbol)
            for bar in bars:
                value = self._calculate_value(
                    spec.name,
                    symbol=symbol,
                    industry=industry,
                    trade_date=bar.trade_date,
                    snapshots_by_dataset_symbol=snapshots_by_dataset_symbol,
                    industries=industries,
                )
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

    def _calculate_value(
        self,
        factor_name: str,
        *,
        symbol: str,
        industry: str | None,
        trade_date: date,
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        industries: dict[str, str],
    ) -> Decimal | None:
        if factor_name == "earnings_revision_breadth":
            if industry is None:
                return None
            return _revision_breadth(
                snapshots_by_dataset_symbol.get(REVISION_DATASET, {}),
                industries,
                industry,
                trade_date,
            )

        if factor_name == "earnings_revision_1m":
            row = _latest_symbol_row(
                snapshots_by_dataset_symbol.get(REVISION_DATASET, {}),
                symbol,
                trade_date,
            )
            return None if row is None else row.value
        if factor_name == "forecast_dispersion":
            row = _latest_symbol_row(
                snapshots_by_dataset_symbol.get(REVISION_DATASET, {}),
                symbol,
                trade_date,
            )
            return None if row is None else _attribute_decimal(row, "forecast_dispersion")
        if factor_name == "guidance_surprise":
            row = _latest_symbol_row(
                snapshots_by_dataset_symbol.get(GUIDANCE_DATASET, {}),
                symbol,
                trade_date,
            )
            return None if row is None else row.value
        if factor_name == "profit_warning_risk":
            row = _latest_symbol_row(
                snapshots_by_dataset_symbol.get(WARNING_DATASET, {}),
                symbol,
                trade_date,
            )
            return None if row is None else row.value
        raise ValueError(f"unsupported factor: {factor_name}")


def _group_symbol_snapshots(rows: list[Any]) -> dict[str, dict[str, list[Any]]]:
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.symbol:
            grouped[row.dataset][row.symbol].append(row)
    for symbols in grouped.values():
        for symbol_rows in symbols.values():
            symbol_rows.sort(key=lambda row: row.snapshot_date)
    return {dataset: dict(symbols) for dataset, symbols in grouped.items()}


def _latest_symbol_row(
    rows_by_symbol: dict[str, list[Any]],
    symbol: str,
    trade_date: date,
) -> Any | None:
    rows = [row for row in rows_by_symbol.get(symbol, []) if row.snapshot_date < trade_date]
    return rows[-1] if rows else None


def _revision_breadth(
    rows_by_symbol: dict[str, list[Any]],
    industries: dict[str, str],
    target_industry: str,
    trade_date: date,
) -> Decimal | None:
    values = []
    for symbol, industry in industries.items():
        if industry != target_industry:
            continue
        row = _latest_symbol_row(rows_by_symbol, symbol, trade_date)
        if row is not None and row.value is not None:
            values.append(row.value)
    if not values:
        return None
    upward = sum(value > 0 for value in values)
    downward = sum(value < 0 for value in values)
    return (Decimal(upward) - Decimal(downward)) / Decimal(len(values))


def _attribute_decimal(row: Any, key: str) -> Decimal | None:
    attributes = row.attributes or {}
    return _decimal(attributes.get(key)) or row.value


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
