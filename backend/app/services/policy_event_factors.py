from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
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
DATASET = "policy_event"
LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class PolicyEventFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    higher_is_better: bool = True
    category: str = FactorCategory.POLICY


@dataclass(frozen=True)
class PolicyEventFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


POLICY_EVENT_FACTOR_SPECS = (
    PolicyEventFactorSpec(
        name="policy_event_score",
        display_name="Policy event score",
        description="latest available industry policy event score",
        inputs=(
            "market.alternative_data_snapshot.policy_event.value",
            "market.security.industry",
        ),
    ),
)


class PolicyEventFactorService:
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
    ) -> PolicyEventFactorRunResult:
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

        industries = self.security_repository.get_industries(db, set(bars_by_symbol))
        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
        snapshots = self.snapshot_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            datasets=[DATASET],
        )
        snapshots_by_industry = _group_industry_snapshots(snapshots)

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
                    "dataset": DATASET,
                    "lookback_days": LOOKBACK_DAYS,
                },
                code_version=code_version,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_policy_event_or_industry",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                    "mapping_source": "current_security.industry",
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                industries=industries,
                snapshots_by_industry=snapshots_by_industry,
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

        return PolicyEventFactorRunResult(
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
    ) -> tuple[PolicyEventFactorSpec, ...]:
        if not factor_names:
            return POLICY_EVENT_FACTOR_SPECS
        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return POLICY_EVENT_FACTOR_SPECS
        available = {spec.name for spec in POLICY_EVENT_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in POLICY_EVENT_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: PolicyEventFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        industries: dict[str, str],
        snapshots_by_industry: dict[str, list[Any]],
        run_id: int,
    ) -> int:
        rows_written = 0
        for symbol, bars in bars_by_symbol.items():
            industry = industries.get(symbol)
            if industry is None:
                continue
            for bar in bars:
                value = self._calculate_value(
                    spec.name,
                    industry=industry,
                    trade_date=bar.trade_date,
                    snapshots_by_industry=snapshots_by_industry,
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
        industry: str,
        trade_date: date,
        snapshots_by_industry: dict[str, list[Any]],
    ) -> Decimal | None:
        if factor_name == "policy_event_score":
            row = _latest_industry_row(snapshots_by_industry, industry, trade_date)
            return None if row is None else row.value
        raise ValueError(f"unsupported factor: {factor_name}")


def _group_industry_snapshots(rows: list[Any]) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        if row.dataset == DATASET and row.item_key and row.value is not None:
            grouped[row.item_key].append(row)
    for industry_rows in grouped.values():
        industry_rows.sort(key=lambda row: row.snapshot_date)
    return dict(grouped)


def _latest_industry_row(
    rows_by_industry: dict[str, list[Any]],
    industry: str,
    trade_date: date,
) -> Any | None:
    rows = [
        row
        for row in rows_by_industry.get(industry, [])
        if row.snapshot_date < trade_date
    ]
    return rows[-1] if rows else None
