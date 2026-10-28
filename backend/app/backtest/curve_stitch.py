"""Stitch per-window backtest equity curves into one combined net curve.

Used by rolling (walk-forward) backtests. Each window's test-period equity
curve is concatenated head-to-tail; the first window starts at ``initial_cash``
and each subsequent window continues from the previous window's ending equity
(**no principal reset**), so the combined total return equals the product of
per-window returns minus one.

Transaction costs are already accrued daily inside the engine, so stitching only
concatenates daily equity points and re-aggregates turnover/commission.

Financial-correctness guard: overlapping test windows are **rejected** (P0). A
naive concatenation of overlapping calendar days would double-count the
overlapping segment's returns and silently inflate the combined curve.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class WindowCostSummary:
    """Per-window cost inputs needed to re-aggregate turnover/commission."""

    commission: Decimal
    traded_notional: Decimal


@dataclass(frozen=True)
class CombinedEquityCurve:
    trade_dates: tuple[date, ...]
    total_equity: tuple[Decimal, ...]
    total_return: Decimal
    annualized_return: Decimal
    sharpe_ratio: Decimal | None
    maximum_drawdown: Decimal
    turnover_rate: Decimal
    total_commission: Decimal
    window_count: int


def _annualized_return(total_return: Decimal, periods: int) -> Decimal:
    if periods <= 0 or total_return <= Decimal(-1):
        return Decimal()
    return Decimal(
        str(float(Decimal(1) + total_return) ** (252 / periods) - 1)
    )


def _maximum_drawdown(equities: Sequence[Decimal]) -> Decimal:
    if not equities:
        return Decimal()
    peak = equities[0]
    maximum_drawdown = Decimal()
    for equity in equities:
        peak = max(peak, equity)
        drawdown = equity / peak - Decimal(1) if peak else Decimal()
        maximum_drawdown = min(maximum_drawdown, drawdown)
    return maximum_drawdown


def stitch_equity_curves(
    window_curves: Sequence[Sequence[tuple[date, Decimal]]],
    window_costs: Sequence[WindowCostSummary],
    initial_cash: Decimal,
) -> CombinedEquityCurve:
    """Concatenate per-window test-period equity curves into one combined curve.

    ``window_curves[i]`` is an ordered list of ``(trade_date, total_equity)`` for
    window ``i``; each window is expected to start at (approximately)
    ``initial_cash``. Windows are ordered by their first date; the first window
    is kept on the ``initial_cash`` basis and every later window is scaled so its
    opening equity equals the previous window's closing equity (principal
    continues, never resets).
    """
    if not window_curves:
        raise ValueError("window_curves must not be empty")
    if len(window_curves) != len(window_costs):
        raise ValueError("window_curves and window_costs length mismatch")
    if initial_cash <= 0:
        raise ValueError("initial_cash must be positive")

    ordered_pairs = sorted(
        zip(window_curves, window_costs, strict=True),
        key=lambda pair: (pair[0][0][0] if pair[0] else date.max),
    )

    combined_dates: list[date] = []
    combined_equity: list[Decimal] = []
    prev_equity = initial_cash

    for curve, _ in ordered_pairs:
        if not curve:
            raise ValueError("each window curve must be non-empty")
        previous_date: date | None = None
        for trade_date, equity in curve:
            if equity <= 0:
                raise ValueError("window curve equity values must be positive")
            if previous_date is not None and trade_date <= previous_date:
                raise ValueError(
                    "window curve trade dates must be strictly increasing"
                )
            previous_date = trade_date
        window_start = curve[0][0]
        if combined_dates:
            if window_start <= combined_dates[-1]:
                raise ValueError(
                    "overlapping test windows cannot be stitched into a combined "
                    "curve; re-run with step_days >= training_days + test_days"
                )
        # Scale this window so it continues from the previous window's equity.
        opening_equity = curve[0][1]
        scale = prev_equity / opening_equity
        for trade_date, equity in curve:
            combined_dates.append(trade_date)
            combined_equity.append(equity * scale)
        prev_equity = combined_equity[-1]

    # Aggregate metrics from the combined (rebased) curve.
    total_return = (
        combined_equity[-1] / initial_cash - Decimal(1)
        if combined_equity
        else Decimal()
    )
    periods = max(len(combined_equity) - 1, 0)
    annualized_return = _annualized_return(total_return, periods)
    daily_returns = [
        combined_equity[i] / combined_equity[i - 1] - Decimal(1)
        for i in range(1, len(combined_equity))
        if combined_equity[i - 1] != 0
    ]
    mean = (
        sum(daily_returns, Decimal()) / Decimal(len(daily_returns))
        if daily_returns
        else Decimal()
    )
    variance = (
        sum((r - mean) ** 2 for r in daily_returns) / Decimal(len(daily_returns))
        if daily_returns
        else Decimal()
    )
    std = variance.sqrt() if variance > 0 else Decimal()
    sharpe_ratio = (
        mean / std * Decimal(str(252**0.5)) if std else None
    )
    maximum_drawdown = _maximum_drawdown(combined_equity)

    average_equity = (
        sum(combined_equity, Decimal()) / Decimal(len(combined_equity))
        if combined_equity
        else initial_cash
    )
    total_traded_notional = sum(
        (cost.traded_notional for cost in window_costs), Decimal()
    )
    turnover_rate = (
        total_traded_notional / average_equity if average_equity else Decimal()
    )
    total_commission = sum(
        (cost.commission for cost in window_costs), Decimal()
    )

    return CombinedEquityCurve(
        trade_dates=tuple(combined_dates),
        total_equity=tuple(combined_equity),
        total_return=total_return,
        annualized_return=annualized_return,
        sharpe_ratio=sharpe_ratio,
        maximum_drawdown=maximum_drawdown,
        turnover_rate=turnover_rate,
        total_commission=total_commission,
        window_count=len(window_curves),
    )


def window_cost_summary_from_trades(
    trades: Iterable,
    commission: Decimal,
) -> WindowCostSummary:
    """Build a :class:`WindowCostSummary` from engine trade rows + total commission."""
    traded_notional = sum(
        (Decimal(str(trade.quantity)) * Decimal(str(trade.fill_price)) for trade in trades),
        Decimal(),
    )
    return WindowCostSummary(
        commission=commission, traded_notional=traded_notional
    )
