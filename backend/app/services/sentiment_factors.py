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
from app.repositories.market import AlternativeDataSnapshotRepository, DailyBarRepository
from app.services.factor_universe import require_price_universe

FACTOR_VERSION = "v1"
VALUE_QUANT = Decimal("0.0000000001")
SUPPORTED_SECURITY_TYPES = ("stock",)
LOOKBACK_DAYS = 120


@dataclass(frozen=True)
class SentimentFactorSpec:
    name: str
    display_name: str
    description: str
    inputs: tuple[str, ...]
    datasets: tuple[str, ...]
    market_wide: bool = False
    higher_is_better: bool = True
    category: str = FactorCategory.SENTIMENT


@dataclass(frozen=True)
class SentimentFactorRunResult:
    start_date: date
    end_date: date
    input_start_date: date
    input_rows: int
    rows_by_factor: dict[str, int]
    run_ids: dict[str, int]

    @property
    def total_rows(self) -> int:
        return sum(self.rows_by_factor.values())


SENTIMENT_FACTOR_SPECS = (
    SentimentFactorSpec(
        name="hot_rank_score",
        display_name="Hot rank score",
        description="1 / latest available hot stock rank",
        inputs=("market.alternative_data_snapshot.hot_stock.rank",),
        datasets=("hot_stock",),
    ),
    SentimentFactorSpec(
        name="hot_heat",
        display_name="Hot heat",
        description="latest available hot stock heat value",
        inputs=("market.alternative_data_snapshot.hot_stock.value",),
        datasets=("hot_stock",),
    ),
    SentimentFactorSpec(
        name="hot_rank_change",
        display_name="Hot rank change",
        description="previous hot stock rank minus latest available hot stock rank",
        inputs=("market.alternative_data_snapshot.hot_stock.rank",),
        datasets=("hot_stock",),
    ),
    SentimentFactorSpec(
        name="limit_up_strength",
        display_name="Limit-up strength",
        description="latest available limit-up pool strength value",
        inputs=("market.alternative_data_snapshot.limit_up_pool.value",),
        datasets=("limit_up_pool",),
    ),
    SentimentFactorSpec(
        name="dragon_tiger_net_buy_ratio",
        display_name="Dragon tiger net buy ratio",
        description="latest available dragon tiger net buy ratio",
        # 只用总榜 ``dragon_tiger``。游资榜 ``dragon_tiger_hot_money`` 是
        # **席位口径**（net_rate = 该席位对该股的净买率），与总榜口径不同；
        # 同一只股票还可能同时出现在两个席位下（唯一键带席位区分）。
        # 把两张榜混进同一个因子，等于让「龙虎榜净买比例」在不同标的上
        # 悄悄换口径——所以这里显式只用总榜，游资数据留给专门的席位因子。
        inputs=("market.alternative_data_snapshot.dragon_tiger.attributes",),
        datasets=("dragon_tiger",),
    ),
    SentimentFactorSpec(
        name="limit_up_breadth",
        display_name="Limit-up breadth",
        description="latest available limit-up pool count / trusted stock universe size",
        inputs=("market.alternative_data_snapshot.limit_up_pool",),
        datasets=("limit_up_pool",),
        market_wide=True,
    ),
    SentimentFactorSpec(
        name="limit_up_failure_rate",
        display_name="Limit-up failure rate",
        description=(
            "latest failed limit-up count / latest failed plus sealed limit-up count"
        ),
        inputs=(
            "market.alternative_data_snapshot.failed_limit_up",
            "market.alternative_data_snapshot.limit_up_pool",
        ),
        datasets=("failed_limit_up", "limit_up_pool"),
        market_wide=True,
        higher_is_better=False,
    ),
    SentimentFactorSpec(
        name="consecutive_limit_up_height",
        display_name="Consecutive limit-up height",
        description="latest maximum consecutive limit-up board height",
        inputs=("market.alternative_data_snapshot.limit_up_ladder",),
        datasets=("limit_up_ladder",),
        market_wide=True,
    ),
    SentimentFactorSpec(
        name="market_sentiment_score",
        display_name="Market sentiment score",
        description=(
            "latest available hot, skyrocket and limit-up counts / trusted "
            "stock universe size"
        ),
        inputs=(
            "market.alternative_data_snapshot.hot_stock",
            "market.alternative_data_snapshot.skyrocket",
            "market.alternative_data_snapshot.limit_up_pool",
        ),
        datasets=("hot_stock", "skyrocket", "limit_up_pool"),
        market_wide=True,
    ),
    SentimentFactorSpec(
        name="sentiment_temperature_percentile",
        display_name="Sentiment temperature percentile",
        description="percentile rank of latest market sentiment score in lookback window",
        inputs=(
            "market.alternative_data_snapshot.hot_stock",
            "market.alternative_data_snapshot.skyrocket",
            "market.alternative_data_snapshot.limit_up_pool",
        ),
        datasets=("hot_stock", "skyrocket", "limit_up_pool"),
        market_wide=True,
        higher_is_better=False,
    ),
    SentimentFactorSpec(
        name="overheat_reversal_signal",
        display_name="Overheat reversal signal",
        description="1 when sentiment temperature is high and failed limit-up rate is elevated",
        inputs=(
            "market.alternative_data_snapshot.hot_stock",
            "market.alternative_data_snapshot.skyrocket",
            "market.alternative_data_snapshot.limit_up_pool",
            "market.alternative_data_snapshot.failed_limit_up",
        ),
        datasets=("hot_stock", "skyrocket", "limit_up_pool", "failed_limit_up"),
        market_wide=True,
        higher_is_better=False,
    ),
)


class SentimentFactorService:
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
    ) -> SentimentFactorRunResult:
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
                    factor_family='sentiment',
                    all_bars=bars,
                )

        input_start_date = start_date - timedelta(days=LOOKBACK_DAYS)
        datasets = sorted({dataset for spec in specs for dataset in spec.datasets})
        snapshots = self.snapshot_repository.list_available(
            db,
            start_date=input_start_date,
            end_date=end_date,
            datasets=datasets,
        )
        snapshots_by_dataset_symbol = _group_symbol_snapshots(snapshots)
        counts_by_dataset_date = _count_symbol_snapshots(snapshots)
        universe_by_date = self.daily_bar_repository.count_trusted_by_date(
            db,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            symbols=normalized_symbols,
        )

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
                window_days=None,
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
                    "lookback_days": LOOKBACK_DAYS,
                },
                code_version=code_version,
                release_id=release_id,
                input_start_date=input_start_date,
                input_end_date=end_date,
                metadata={
                    "missing_policy": "skip_missing_snapshot_or_required_field",
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
                counts_by_dataset_date=counts_by_dataset_date,
                universe_by_date=universe_by_date,
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

        return SentimentFactorRunResult(
            start_date=start_date,
            end_date=end_date,
            input_start_date=input_start_date,
            input_rows=len(snapshots),
            rows_by_factor=rows_by_factor,
            run_ids=run_ids,
        )

    def _select_specs(
        self, factor_names: list[str] | None
    ) -> tuple[SentimentFactorSpec, ...]:
        if not factor_names:
            return SENTIMENT_FACTOR_SPECS

        requested = {name.strip() for name in factor_names if name.strip()}
        if not requested:
            return SENTIMENT_FACTOR_SPECS
        available = {spec.name for spec in SENTIMENT_FACTOR_SPECS}
        unknown = sorted(requested - available)
        if unknown:
            raise ValueError(f"unknown factor names: {', '.join(unknown)}")
        return tuple(spec for spec in SENTIMENT_FACTOR_SPECS if spec.name in requested)

    def _write_factor_values(
        self,
        db: Session,
        *,
        spec: SentimentFactorSpec,
        bars_by_symbol: dict[str, list[Any]],
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        counts_by_dataset_date: dict[str, dict[date, int]],
        universe_by_date: dict[date, int],
        run_id: int,
    ) -> int:
        pending_rows: list[dict[str, Any]] = []
        for symbol, bars in bars_by_symbol.items():
            for bar in bars:
                value = self._calculate_value(
                    spec,
                    symbol=symbol,
                    trade_date=bar.trade_date,
                    snapshots_by_dataset_symbol=snapshots_by_dataset_symbol,
                    counts_by_dataset_date=counts_by_dataset_date,
                    universe_by_date=universe_by_date,
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
        spec: SentimentFactorSpec,
        *,
        symbol: str,
        trade_date: date,
        snapshots_by_dataset_symbol: dict[str, dict[str, list[Any]]],
        counts_by_dataset_date: dict[str, dict[date, int]],
        universe_by_date: dict[date, int],
    ) -> Decimal | None:
        if spec.name == "limit_up_breadth":
            universe_size = universe_by_date.get(trade_date)
            if not universe_size:
                return None
            count = _latest_count(counts_by_dataset_date.get("limit_up_pool", {}), trade_date)
            return None if count is None else Decimal(count) / Decimal(universe_size)
        if spec.name == "limit_up_failure_rate":
            return _limit_up_failure_rate(counts_by_dataset_date, trade_date)
        if spec.name == "consecutive_limit_up_height":
            return _consecutive_limit_up_height(
                snapshots_by_dataset_symbol.get("limit_up_ladder", {}),
                trade_date,
            )
        if spec.name == "market_sentiment_score":
            universe_size = universe_by_date.get(trade_date)
            if not universe_size:
                return None
            return _market_sentiment_score(
                counts_by_dataset_date,
                trade_date,
                universe_size,
            )
        if spec.name == "sentiment_temperature_percentile":
            universe_size = universe_by_date.get(trade_date)
            if not universe_size:
                return None
            scores = _sentiment_history_scores(
                counts_by_dataset_date,
                trade_date,
                universe_size,
            )
            if not scores:
                return None
            return _percentile_rank(scores, scores[-1])
        if spec.name == "overheat_reversal_signal":
            universe_size = universe_by_date.get(trade_date)
            if not universe_size:
                return None
            scores = _sentiment_history_scores(
                counts_by_dataset_date,
                trade_date,
                universe_size,
            )
            failure_rate = _limit_up_failure_rate(counts_by_dataset_date, trade_date)
            if not scores or failure_rate is None:
                return None
            temperature = _percentile_rank(scores, scores[-1])
            return (
                Decimal(1)
                if temperature >= Decimal("0.8") and failure_rate >= Decimal("0.3")
                else Decimal()
            )

        row = _latest_symbol_row(
            snapshots_by_dataset_symbol,
            datasets=spec.datasets,
            symbol=symbol,
            trade_date=trade_date,
        )
        if row is None:
            return None
        if spec.name == "hot_rank_score":
            return None if row.rank is None or row.rank <= 0 else Decimal(1) / Decimal(row.rank)
        if spec.name == "hot_heat":
            return row.value
        if spec.name == "hot_rank_change":
            previous_row = _previous_symbol_row(
                snapshots_by_dataset_symbol,
                datasets=spec.datasets,
                symbol=symbol,
                trade_date=trade_date,
            )
            if row.rank is None or previous_row is None:
                return None
            if previous_row.rank is None or row.rank <= 0 or previous_row.rank <= 0:
                return None
            return Decimal(previous_row.rank - row.rank)
        if spec.name == "limit_up_strength":
            return row.value
        if spec.name == "dragon_tiger_net_buy_ratio":
            return _dragon_tiger_net_buy_ratio(row)
        raise ValueError(f"unsupported factor: {spec.name}")


def _group_symbol_snapshots(rows: list[Any]) -> dict[str, dict[str, list[Any]]]:
    grouped: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.symbol:
            grouped[row.dataset][row.symbol].append(row)
    for symbols in grouped.values():
        for symbol_rows in symbols.values():
            symbol_rows.sort(key=lambda row: row.snapshot_date)
    return {dataset: dict(symbols) for dataset, symbols in grouped.items()}


def _count_symbol_snapshots(rows: list[Any]) -> dict[str, dict[date, int]]:
    symbols_by_dataset_date: dict[str, dict[date, set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    for row in rows:
        if row.symbol:
            symbols_by_dataset_date[row.dataset][row.snapshot_date].add(row.symbol)
    return {
        dataset: {
            snapshot_date: len(symbols)
            for snapshot_date, symbols in date_symbols.items()
        }
        for dataset, date_symbols in symbols_by_dataset_date.items()
    }


def _latest_symbol_row(
    rows_by_dataset_symbol: dict[str, dict[str, list[Any]]],
    *,
    datasets: tuple[str, ...],
    symbol: str,
    trade_date: date,
) -> Any | None:
    candidates = []
    for dataset in datasets:
        rows = rows_by_dataset_symbol.get(dataset, {}).get(symbol, [])
        candidates.extend(row for row in rows if row.snapshot_date < trade_date)
    if not candidates:
        return None
    return max(candidates, key=lambda row: (row.snapshot_date, row.dataset))


def _previous_symbol_row(
    rows_by_dataset_symbol: dict[str, dict[str, list[Any]]],
    *,
    datasets: tuple[str, ...],
    symbol: str,
    trade_date: date,
) -> Any | None:
    candidates = []
    for dataset in datasets:
        rows = rows_by_dataset_symbol.get(dataset, {}).get(symbol, [])
        candidates.extend(row for row in rows if row.snapshot_date < trade_date)
    if len(candidates) < 2:
        return None
    candidates.sort(key=lambda row: (row.snapshot_date, row.dataset))
    return candidates[-2]


def _latest_count(counts_by_date: dict[date, int], trade_date: date) -> int | None:
    available_dates = [
        snapshot_date for snapshot_date in counts_by_date if snapshot_date < trade_date
    ]
    if not available_dates:
        return None
    return counts_by_date[max(available_dates)]


def _market_sentiment_score(
    counts_by_dataset_date: dict[str, dict[date, int]],
    trade_date: date,
    universe_size: int,
) -> Decimal | None:
    counts = [
        _latest_count(counts_by_dataset_date.get(dataset, {}), trade_date)
        for dataset in ("hot_stock", "skyrocket", "limit_up_pool")
    ]
    available = [count for count in counts if count is not None]
    if not available:
        return None
    return Decimal(sum(available)) / Decimal(universe_size)


def _limit_up_failure_rate(
    counts_by_dataset_date: dict[str, dict[date, int]],
    trade_date: date,
) -> Decimal | None:
    failed = _latest_count(counts_by_dataset_date.get("failed_limit_up", {}), trade_date)
    sealed = _latest_count(counts_by_dataset_date.get("limit_up_pool", {}), trade_date)
    if failed is None or sealed is None or failed + sealed == 0:
        return None
    return Decimal(failed) / Decimal(failed + sealed)


def _sentiment_history_scores(
    counts_by_dataset_date: dict[str, dict[date, int]],
    trade_date: date,
    universe_size: int,
) -> list[Decimal]:
    snapshot_dates = sorted(
        {
            snapshot_date
            for dataset in ("hot_stock", "skyrocket", "limit_up_pool")
            for snapshot_date in counts_by_dataset_date.get(dataset, {})
            if snapshot_date < trade_date
        }
    )
    scores = []
    for snapshot_date in snapshot_dates:
        score = _market_sentiment_score(
            counts_by_dataset_date,
            snapshot_date + timedelta(days=1),
            universe_size,
        )
        if score is not None:
            scores.append(score)
    return scores


def _consecutive_limit_up_height(
    rows_by_symbol: dict[str, list[Any]],
    trade_date: date,
) -> Decimal | None:
    rows = [
        row
        for rows in rows_by_symbol.values()
        for row in rows
        if row.snapshot_date < trade_date
    ]
    if not rows:
        return None
    latest_date = max(row.snapshot_date for row in rows)
    heights = [
        height
        for row in rows
        if row.snapshot_date == latest_date
        for height in [_limit_up_height(row)]
        if height is not None
    ]
    return max(heights) if heights else None


def _limit_up_height(row: Any) -> Decimal | None:
    if row.value is not None:
        return row.value
    attributes = row.attributes or {}
    return _decimal(
        attributes.get("continue_day_cnt")
        or attributes.get("height")
        or attributes.get("board")
    )


def _dragon_tiger_net_buy_ratio(row: Any) -> Decimal | None:
    attributes = row.attributes or {}
    explicit = _decimal(
        attributes.get("net_rate")
        or attributes.get("net_buy_ratio")
        or attributes.get("net_value_rate")
    )
    if explicit is not None:
        return explicit
    net_value = _decimal(attributes.get("net_value")) or row.value
    buy_value = _decimal(attributes.get("buy_value"))
    sell_value = _decimal(attributes.get("sell_value"))
    if net_value is None or buy_value is None or sell_value is None:
        return None
    denominator = abs(buy_value) + abs(sell_value)
    if denominator == 0:
        return None
    return net_value / denominator


def _percentile_rank(values: list[Decimal], current: Decimal) -> Decimal:
    if len(values) == 1:
        return Decimal("0.5")
    below = sum(value < current for value in values)
    equal = sum(value == current for value in values)
    average_rank = Decimal(below) + (Decimal(equal - 1) / Decimal(2))
    return average_rank / Decimal(len(values) - 1)


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
