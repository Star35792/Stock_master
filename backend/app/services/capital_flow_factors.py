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
DATASET = "eastmoney_main_fund_rank"
NORTHBOUND_DATASET = "northbound_flow"
MARGIN_DATASET = "margin_financing"
ETF_INDUSTRY_DATASET = "etf_flow_industry"
INSTITUTIONAL_CROWDING_DATASET = "institutional_crowding"
LOOKBACK_DAYS = 90


@dataclass(frozen=True)
class CapitalFlowFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    datasets: tuple[str, ...]
    window_days: int | None = None
    higher_is_better: bool = True
    category: str = FactorCategory.CAPITAL_FLOW


@dataclass(frozen=True)
class CapitalFlowFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


CAPITAL_FLOW_FACTOR_SPECS = (
    CapitalFlowFactorSpec(
        name="main_fund_net_inflow",
        display_name="Main fund net inflow",
        description="latest available Eastmoney main fund net inflow",
        inputs=("market.alternative_data_snapshot.eastmoney_main_fund_rank.value",),
        datasets=(DATASET,),
    ),
    CapitalFlowFactorSpec(
        name="main_fund_net_inflow_ratio",
        display_name="Main fund net inflow ratio",
        description="latest main fund net inflow / latest prior trusted amount",
        inputs=(
            "market.alternative_data_snapshot.eastmoney_main_fund_rank.value",
            "market.daily_bar.amount",
        ),
        datasets=(DATASET,),
    ),
    CapitalFlowFactorSpec(
        name="main_fund_rank_score",
        display_name="Main fund rank score",
        description="1 / latest available Eastmoney main fund rank",
        inputs=("market.alternative_data_snapshot.eastmoney_main_fund_rank.rank",),
        datasets=(DATASET,),
    ),
    CapitalFlowFactorSpec(
        name="main_fund_inflow_change_5",
        display_name="Main fund inflow change 5",
        description="latest main fund net inflow minus 5-observation lag",
        inputs=("market.alternative_data_snapshot.eastmoney_main_fund_rank.value",),
        datasets=(DATASET,),
        window_days=5,
    ),
    CapitalFlowFactorSpec(
        name="main_fund_inflow_percentile_20",
        display_name="Main fund inflow percentile 20",
        description="percentile rank of latest main fund net inflow over 20 observations",
        inputs=("market.alternative_data_snapshot.eastmoney_main_fund_rank.value",),
        datasets=(DATASET,),
        window_days=20,
    ),
    CapitalFlowFactorSpec(
        name="northbound_flow_ratio",
        display_name="Northbound flow ratio",
        description="latest northbound net inflow / latest prior trusted amount",
        inputs=(
            "market.alternative_data_snapshot.northbound_flow.value",
            "market.daily_bar.amount",
        ),
        datasets=(NORTHBOUND_DATASET,),
    ),
    CapitalFlowFactorSpec(
        name="margin_financing_change_20",
        display_name="Margin financing change 20",
        description="latest margin financing balance minus 20-observation lag",
        inputs=("market.alternative_data_snapshot.margin_financing.value",),
        datasets=(MARGIN_DATASET,),
        window_days=20,
    ),
    CapitalFlowFactorSpec(
        name="etf_flow_industry_20",
        display_name="ETF flow industry 20",
        description="20-observation sum of latest industry ETF net flow",
        inputs=(
            "market.alternative_data_snapshot.etf_flow_industry.value",
            "market.security.industry",
        ),
        datasets=(ETF_INDUSTRY_DATASET,),
        window_days=20,
    ),
    CapitalFlowFactorSpec(
        name="institutional_crowding_score",
        display_name="Institutional crowding score",
        description="latest institutional crowding proxy score",
        inputs=("market.alternative_data_snapshot.institutional_crowding.value",),
        datasets=(INSTITUTIONAL_CROWDING_DATASET,),
        higher_is_better=False,
    ),
)


class CapitalFlowFactorService:
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
    ) -> CapitalFlowFactorRunResult:
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
        for symbol_bars in bars_by_symbol.values():
            symbol_bars.sort(key=lambda bar: bar.trade_date)

        datasets = sorted({dataset for spec in specs for dataset in spec.datasets})
        snapshots = self.snapshot_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            datasets=datasets,
        )
        snapshots_by_dataset_symbol = _group_symbol_snapshots(snapshots)
        snapshots_by_dataset_item = _group_item_snapshots(snapshots)
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
                window_days=spec.window_days,
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
                    "missing_policy": "skip_missing_flow_snapshot_or_amount",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                snapshots_by_dataset_symbol=snapshots_by_dataset_symbol,
                snapshots_by_dataset_item=snapshots_by_dataset_item,
                industries=industries,
                run_id=run.id,
                start_date=start_date,
                end_date=end_date,
            )
            self.run_repository.update_status(
                db,
                run_id=run.id,
                status="success",
                rows_written=rows_written,
            )
            rows_by_factor[spec.name] = rows_written
            run_ids[spec.name] = run.id

        return CapitalFlowFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(snapshots),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(
        self, factor_names: list[str] | None
    ) -> tuple[CapitalFlowFactorSpec, ...]:
        if not factor_names:
            return CAPITAL_FLOW_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return CAPITAL_FLOW_FACTOR_SPECS
        available = {spec.name for spec in CAPITAL_FLOW_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in CAPITAL_FLOW_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: CapitalFlowFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        snapshots_by_dataset_item: dict[str, dict[str, list[Any]]],
        industries: dict[str, str],
        run_id: int,
        start_date: date,
        end_date: date,
    ) -> int:
        rows_written = 0
        for symbol, bars in bars_by_symbol.items():
            industry = industries.get(symbol)
            for index, bar in enumerate(bars):
                if bar.trade_date < start_date or bar.trade_date > end_date:
                    continue
                value = self._calculate_value(
                    spec.name,
                    bars=bars,
                    bar_index=index,
                    symbol=symbol,
                    industry=industry,
                    snapshots_by_dataset_symbol=snapshots_by_dataset_symbol,
                    snapshots_by_dataset_item=snapshots_by_dataset_item,
                    trade_date=bar.trade_date,
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
        bars: list[Any],
        bar_index: int,
        symbol: str,
        industry: str | None,
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        snapshots_by_dataset_item: dict[str, dict[str, list[Any]]],
        trade_date: date,
    ) -> Decimal | None:
        snapshots = snapshots_by_dataset_symbol.get(DATASET, {}).get(symbol, [])
        row = _latest_snapshot(snapshots, trade_date)
        if factor_name == "main_fund_net_inflow":
            if row is None:
                return None
            return row.value
        if factor_name == "main_fund_net_inflow_ratio":
            if row is None:
                return None
            amount = _latest_prior_amount(bars, bar_index)
            if row.value is None or amount is None or amount == 0:
                return None
            return row.value / amount
        if factor_name == "main_fund_rank_score":
            if row is None:
                return None
            return None if row.rank is None or row.rank <= 0 else Decimal(1) / Decimal(row.rank)
        if factor_name == "main_fund_inflow_change_5":
            pair = _lagged_snapshot_values(snapshots, trade_date, lag_observations=5)
            return None if pair is None else pair[0] - pair[1]
        if factor_name == "main_fund_inflow_percentile_20":
            if row is None:
                return None
            values = _latest_snapshot_values(snapshots, trade_date, limit=20)
            if len(values) < 20 or row.value is None:
                return None
            return _percentile_rank(values, row.value)
        if factor_name == "northbound_flow_ratio":
            northbound_row = _latest_snapshot(
                snapshots_by_dataset_symbol.get(NORTHBOUND_DATASET, {}).get(symbol, []),
                trade_date,
            )
            amount = _latest_prior_amount(bars, bar_index)
            if northbound_row is None or northbound_row.value is None:
                return None
            if amount is None or amount == 0:
                return None
            return northbound_row.value / amount
        if factor_name == "margin_financing_change_20":
            pair = _lagged_snapshot_values(
                snapshots_by_dataset_symbol.get(MARGIN_DATASET, {}).get(symbol, []),
                trade_date,
                lag_observations=20,
            )
            return None if pair is None else pair[0] - pair[1]
        if factor_name == "etf_flow_industry_20":
            if industry is None:
                return None
            values = _latest_snapshot_values(
                snapshots_by_dataset_item.get(ETF_INDUSTRY_DATASET, {}).get(industry, []),
                trade_date,
                limit=20,
            )
            return None if len(values) < 20 else sum(values, Decimal())
        if factor_name == "institutional_crowding_score":
            crowding_row = _latest_snapshot(
                snapshots_by_dataset_symbol.get(INSTITUTIONAL_CROWDING_DATASET, {}).get(
                    symbol,
                    [],
                ),
                trade_date,
            )
            return None if crowding_row is None else crowding_row.value
        raise ValueError(f"unsupported factor: {factor_name}")


def _group_symbol_snapshots(rows: list[Any]) -> dict[str, dict[str, list[Any]]]:
    grouped: dict[str, dict[str, dict[date, Any]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        if row.symbol is None or row.value is None:
            continue
        grouped[row.dataset][row.symbol][row.snapshot_date] = row
    return {
        dataset: {
            symbol: [date_rows[trade_date] for trade_date in sorted(date_rows)]
            for symbol, date_rows in symbol_rows.items()
        }
        for dataset, symbol_rows in grouped.items()
    }


def _group_item_snapshots(rows: list[Any]) -> dict[str, dict[str, list[Any]]]:
    grouped: dict[str, dict[str, dict[date, Any]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for row in rows:
        if row.value is None:
            continue
        grouped[row.dataset][row.item_key][row.snapshot_date] = row
    return {
        dataset: {
            item_key: [date_rows[trade_date] for trade_date in sorted(date_rows)]
            for item_key, date_rows in item_rows.items()
        }
        for dataset, item_rows in grouped.items()
    }


def _latest_snapshot(rows: list[Any], trade_date: date) -> Any | None:
    available = [row for row in rows if row.snapshot_date < trade_date]
    return available[-1] if available else None


def _latest_snapshot_values(
    rows: list[Any],
    trade_date: date,
    *,
    limit: int,
) -> list[Decimal]:
    values = [
        row.value
        for row in rows
        if row.snapshot_date < trade_date and row.value is not None
    ]
    return values[-limit:]


def _lagged_snapshot_values(
    rows: list[Any],
    trade_date: date,
    *,
    lag_observations: int,
) -> tuple[Decimal, Decimal] | None:
    values = _latest_snapshot_values(rows, trade_date, limit=lag_observations + 1)
    if len(values) <= lag_observations:
        return None
    return values[-1], values[-1 - lag_observations]


def _latest_prior_amount(bars: list[Any], index: int) -> Decimal | None:
    for bar in reversed(bars[:index]):
        if bar.amount is not None and bar.amount > 0:
            return bar.amount
    return None


def _percentile_rank(values: list[Decimal], current: Decimal) -> Decimal:
    if len(values) == 1:
        return Decimal("0.5")
    below = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    average_rank = Decimal(below) + (Decimal(equal - 1) / Decimal(2))
    return average_rank / Decimal(len(values) - 1)
