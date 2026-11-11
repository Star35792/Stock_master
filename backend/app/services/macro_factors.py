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
from app.repositories.market import AlternativeDataSnapshotRepository, DailyBarRepository
from app.services.factor_universe import require_price_universe

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock",)
DATASET = "macro_indicator"
LOOKBACK_DAYS = 730
TREND_OBSERVATIONS = 20

CN_10Y_YIELD = "cn_10y_yield"
CN_1Y_YIELD = "cn_1y_yield"
SOCIAL_FINANCING_YOY = "social_financing_yoy"
M1_YOY = "m1_yoy"
M2_YOY = "m2_yoy"
USD_CNH = "usd_cnh"
COMMODITY_PROXY = "commodity_proxy"
CREDIT_SPREAD = "credit_spread"


@dataclass(frozen=True)
class MacroFactorSpec:
    name: str
    display_name: str
    description: str
    item_keys: tuple[str, ...]
    higher_is_better: bool = True
    category: str = FactorCategory.MACRO


@dataclass(frozen=True)
class MacroFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


MACRO_FACTOR_SPECS = (
    MacroFactorSpec(
        name="rate_trend_10y",
        display_name="10Y rate trend",
        description="latest available 10Y China government bond yield minus 20-observation lag",
        item_keys=(CN_10Y_YIELD,),
        higher_is_better=False,
    ),
    MacroFactorSpec(
        name="yield_curve_slope",
        display_name="Yield curve slope",
        description="latest available 10Y China government bond yield minus 1Y yield",
        item_keys=(CN_10Y_YIELD, CN_1Y_YIELD),
    ),
    MacroFactorSpec(
        name="social_financing_impulse",
        display_name="Social financing impulse",
        description="latest social financing YoY minus previous available observation",
        item_keys=(SOCIAL_FINANCING_YOY,),
    ),
    MacroFactorSpec(
        name="m1_m2_gap",
        display_name="M1-M2 gap",
        description="latest M1 YoY minus latest M2 YoY",
        item_keys=(M1_YOY, M2_YOY),
    ),
    MacroFactorSpec(
        name="usd_cnh_trend",
        display_name="USD/CNH trend",
        description="latest USD/CNH divided by 20-observation lag minus 1",
        item_keys=(USD_CNH,),
        higher_is_better=False,
    ),
    MacroFactorSpec(
        name="commodity_inflation_proxy",
        display_name="Commodity inflation proxy",
        description="latest commodity proxy divided by 20-observation lag minus 1",
        item_keys=(COMMODITY_PROXY,),
    ),
    MacroFactorSpec(
        name="credit_spread_change",
        display_name="Credit spread change",
        description="latest credit spread minus 20-observation lag",
        item_keys=(CREDIT_SPREAD,),
        higher_is_better=False,
    ),
)


class MacroFactorService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        snapshot_repository: AlternativeDataSnapshotRepository | None = None,
        definition_repository: FactorDefinitionRepository | None = None,
        run_repository: FactorRunRepository | None = None,
        value_repository: FactorValueRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.snapshot_repository = snapshot_repository or AlternativeDataSnapshotRepository()
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
    ) -> MacroFactorRunResult:
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
                require_price_universe(
                    bars_by_symbol,
                    adjust_type=adjust_type,
                    start_date=start_date,
                    end_date=end_date,
                    factor_family='macro',
                    all_bars=bars,
                )

        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
        snapshots = self.snapshot_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            datasets=[DATASET],
        )
        values_by_key = _group_macro_values(snapshots)

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
                inputs=[
                    f"market.alternative_data_snapshot.{DATASET}.{key}"
                    for key in spec.item_keys
                ],
                applicable_security_types=list(SUPPORTED_SECURITY_TYPES),
                window_days=TREND_OBSERVATIONS if _uses_trend_window(spec.name) else None,
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
                    "trend_observations": TREND_OBSERVATIONS,
                },
                code_version=code_version,
                release_id=release_id,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_macro_observation",
                    "applicable_security_types": list(SUPPORTED_SECURITY_TYPES),
                    "higher_is_better": spec.higher_is_better,
                    "market_wide": True,
                },
            )
            rows_written = self._write_factor_values(
                db,
                spec=spec,
                bars_by_symbol=bars_by_symbol,
                values_by_key=values_by_key,
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

        return MacroFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(snapshots),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(self, factor_names: list[str] | None) -> tuple[MacroFactorSpec, ...]:
        if not factor_names:
            return MACRO_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return MACRO_FACTOR_SPECS
        available = {spec.name for spec in MACRO_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in MACRO_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: MacroFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        values_by_key: dict[str, list[tuple[date, Decimal]]],
        run_id: int,
    ) -> int:
        pending_rows: list[dict[str, Any]] = []
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                value = self._calculate_value(
                    spec.name,
                    trade_date=bar.trade_date,
                    values_by_key=values_by_key,
                )
                if value is None:
                    continue
                pending_rows.append(
                    {
                        "symbol": symbol,
                        "trade_date": bar.trade_date,
                        "factor_name": spec.name,
                        "factor_version": FACTOR_VERSION,
                        "value": value.quantize(VALUE_QUANT),
                        "run_id": run_id,
                    }
                )
        return self.value_repository.upsert_values(db, pending_rows)

    def _calculate_value(
        self,
        factor_name: str,
        *,
        trade_date: date,
        values_by_key: dict[str, list[tuple[date, Decimal]]],
    ) -> Decimal | None:
        if factor_name == "rate_trend_10y":
            return _trend_delta(values_by_key.get(CN_10Y_YIELD, []), trade_date)
        if factor_name == "yield_curve_slope":
            ten_year = _latest_value(values_by_key.get(CN_10Y_YIELD, []), trade_date)
            one_year = _latest_value(values_by_key.get(CN_1Y_YIELD, []), trade_date)
            return None if ten_year is None or one_year is None else ten_year - one_year
        if factor_name == "social_financing_impulse":
            return _trend_delta(
                values_by_key.get(SOCIAL_FINANCING_YOY, []),
                trade_date,
                lag_observations=1,
            )
        if factor_name == "m1_m2_gap":
            m1 = _latest_value(values_by_key.get(M1_YOY, []), trade_date)
            m2 = _latest_value(values_by_key.get(M2_YOY, []), trade_date)
            return None if m1 is None or m2 is None else m1 - m2
        if factor_name == "usd_cnh_trend":
            return _trend_ratio(values_by_key.get(USD_CNH, []), trade_date)
        if factor_name == "commodity_inflation_proxy":
            return _trend_ratio(values_by_key.get(COMMODITY_PROXY, []), trade_date)
        if factor_name == "credit_spread_change":
            return _trend_delta(values_by_key.get(CREDIT_SPREAD, []), trade_date)
        raise ValueError(f"unsupported factor: {factor_name}")


def _group_macro_values(rows: list[Any]) -> dict[str, list[tuple[date, Decimal]]]:
    grouped: dict[str, dict[date, Decimal]] = defaultdict(dict)
    for row in rows:
        if row.dataset != DATASET or row.value is None:
            continue
        grouped[row.item_key][row.snapshot_date] = row.value
    return {
        item_key: sorted(date_values.items())
        for item_key, date_values in grouped.items()
    }


def _latest_value(rows: list[tuple[date, Decimal]], trade_date: date) -> Decimal | None:
    available = [value for snapshot_date, value in rows if snapshot_date < trade_date]
    return available[-1] if available else None


def _lagged_pair(
    rows: list[tuple[date, Decimal]],
    trade_date: date,
    *,
    lag_observations: int,
) -> tuple[Decimal, Decimal] | None:
    available = [value for snapshot_date, value in rows if snapshot_date < trade_date]
    if len(available) <= lag_observations:
        return None
    return available[-1], available[-1 - lag_observations]


def _trend_delta(
    rows: list[tuple[date, Decimal]],
    trade_date: date,
    *,
    lag_observations: int = TREND_OBSERVATIONS,
) -> Decimal | None:
    pair = _lagged_pair(rows, trade_date, lag_observations=lag_observations)
    return None if pair is None else pair[0] - pair[1]


def _trend_ratio(
    rows: list[tuple[date, Decimal]],
    trade_date: date,
    *,
    lag_observations: int = TREND_OBSERVATIONS,
) -> Decimal | None:
    pair = _lagged_pair(rows, trade_date, lag_observations=lag_observations)
    if pair is None or pair[1] == 0:
        return None
    return pair[0] / pair[1] - Decimal(1)


def _uses_trend_window(factor_name: str) -> bool:
    return factor_name in {
        "rate_trend_10y",
        "usd_cnh_trend",
        "commodity_inflation_proxy",
        "credit_spread_change",
    }
