"""Data structures for quantile (factor-layer) backtest results.

These are the in-memory result objects produced by
:mod:`app.backtest.quantile` partitioning plus the per-layer engine runs inside
``DailyBacktestService``. They are converted to API read models and embedded
into ``BacktestRun.parameters`` for auditability.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.schemas.strategy import QuantileBacktestResultRead, QuantileLayerRead


@dataclass(frozen=True)
class QuantileBacktestMeta:
    """Static description of how the universe was bucketed."""

    split_factor_name: str
    split_factor_version: str
    n_layers: int
    max_assets_per_layer: int | None
    layer_bounds: tuple[tuple[Decimal, Decimal], ...]


@dataclass(frozen=True)
class QuantileLayerResult:
    layer_index: int
    quantile_low: Decimal
    quantile_high: Decimal
    universe_size: int
    asset_count: int
    top_asset_symbols: tuple[str, ...]
    equity_curve: tuple[Any, ...]  # tuple[EquitySnapshot, ...]
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    sharpe_ratio: Decimal | None
    turnover_rate: Decimal
    total_commission: Decimal


@dataclass(frozen=True)
class QuantileBacktestResult:
    split_factor_name: str
    split_factor_version: str
    n_layers: int
    layers: tuple[QuantileLayerResult, ...]
    long_short_spread_return: Decimal
    spread_curve: tuple[tuple[date, Decimal], ...]
    declarations: dict[str, str]


# Hard financial-correctness declarations embedded into every quantile result.
QUANTILE_DECLARATIONS: dict[str, str] = {
    "long_short": (
        "A-shares cannot be shorted; the top-minus-bottom return is a "
        "statistical spread (top layer net return minus bottom layer net "
        "return), NOT a tradable long-short position."
    ),
    "bias_disclosure": (
        "Quantile layer returns do NOT deduct survivor, ST/halt, or industry "
        "point-in-time biases. The status facts are still missing: "
        "security_status_event=0 rows, so a halt can only be inferred from a "
        "missing bar. The industry labels are present "
        "(security_industry_history=5,579 rows) but they are a current-label "
        "backfill (source=current_label_backfill_20261024), so they are NOT "
        "point-in-time labels and industry neutralisation on them is not "
        "trustworthy. The ST state is available only for the CURRENT status "
        "(derived from the security short name, 200 ST + 1 delisting among "
        "the listed companies); its dated history is unavailable because the "
        "supplier serves the current name on every historical row. "
        "Corporate-action event accounting is unimplemented and intervals "
        "containing it are rejected, so samples carry selection-survivor bias."
    ),
    "method": (
        "Each layer is an independent equal-weight long-only portfolio of that "
        "bucket's symbols, executed T+1 like the main selection; symbols leaving "
        "a bucket receive explicit zero-weight exit targets; layer curves are net "
        "of fees/slippage/taxes."
    ),
    "position_target_policy": "explicit_zero_exit_v2",
}


def quantile_result_to_json(result: QuantileBacktestResult) -> dict[str, Any]:
    """Serialize a :class:`QuantileBacktestResult` for ``parameters`` JSON."""

    def curve_to_list(curve: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [
            {"trade_date": point.trade_date.isoformat(), "total_equity": str(point.total_equity)}
            for point in curve
        ]

    return {
        "split_factor_name": result.split_factor_name,
        "split_factor_version": result.split_factor_version,
        "n_layers": result.n_layers,
        "layers": [
            {
                "layer_index": layer.layer_index,
                "quantile_low": str(layer.quantile_low),
                "quantile_high": str(layer.quantile_high),
                "universe_size": layer.universe_size,
                "asset_count": layer.asset_count,
                "top_asset_symbols": list(layer.top_asset_symbols),
                "equity_curve": curve_to_list(layer.equity_curve),
                "total_return": str(layer.total_return),
                "annualized_return": str(layer.annualized_return),
                "maximum_drawdown": str(layer.maximum_drawdown),
                "sharpe_ratio": (
                    str(layer.sharpe_ratio) if layer.sharpe_ratio is not None else None
                ),
                "turnover_rate": str(layer.turnover_rate),
                "total_commission": str(layer.total_commission),
            }
            for layer in result.layers
        ],
        "long_short_spread_return": str(result.long_short_spread_return),
        "spread_curve": [
            {"trade_date": trade_date.isoformat(), "spread_equity": str(equity)}
            for trade_date, equity in result.spread_curve
        ],
        "declarations": result.declarations,
    }


def _quantile_curve_to_list(curve: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [
        {"trade_date": point.trade_date.isoformat(), "total_equity": str(point.total_equity)}
        for point in curve
    ]


def quantile_result_to_read(result: QuantileBacktestResult) -> QuantileBacktestResultRead:
    """Convert an in-memory :class:`QuantileBacktestResult` to its API read model."""
    return QuantileBacktestResultRead(
        split_factor_name=result.split_factor_name,
        split_factor_version=result.split_factor_version,
        n_layers=result.n_layers,
        layers=[
            QuantileLayerRead(
                layer_index=layer.layer_index,
                quantile_low=layer.quantile_low,
                quantile_high=layer.quantile_high,
                universe_size=layer.universe_size,
                asset_count=layer.asset_count,
                top_asset_symbols=list(layer.top_asset_symbols),
                equity_curve=_quantile_curve_to_list(layer.equity_curve),
                total_return=layer.total_return,
                annualized_return=layer.annualized_return,
                maximum_drawdown=layer.maximum_drawdown,
                sharpe_ratio=layer.sharpe_ratio,
                turnover_rate=layer.turnover_rate,
                total_commission=layer.total_commission,
            )
            for layer in result.layers
        ],
        long_short_spread_return=result.long_short_spread_return,
        spread_curve=[
            {"trade_date": trade_date.isoformat(), "spread_equity": str(equity)}
            for trade_date, equity in result.spread_curve
        ],
        declarations=result.declarations,
    )
