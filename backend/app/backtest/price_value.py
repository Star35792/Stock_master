"""Single-security, assumption-based replay. No broker or portfolio writes."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, localcontext
from typing import Any

from app.backtest.engine import (
    BacktestResult,
    BacktestTrade,
    EquitySnapshot,
    PositionSnapshot,
)
from app.backtest.fees import AShareCostFeeModel
from app.schemas.price_value import PriceValueRequest
from app.schemas.price_value_backtest import (
    PriceValueAssumption,
    PriceValueBacktestRequest,
    PriceValueDecision,
)
from app.strategies.price_value import calculate_valuation

ENGINE_VERSION = "price_value_single_asset_v1"
SHANGHAI = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class _Intent:
    signal_date: date
    assumption: PriceValueAssumption
    ceiling: Decimal | None = None


def _valuation_inputs(
    payload: PriceValueBacktestRequest, assumption: PriceValueAssumption
) -> PriceValueRequest:
    return PriceValueRequest(
        symbol=payload.symbol,
        trade_date=payload.start_date,
        valuation_at=assumption.valuation_at,
        assumptions_available_at=assumption.available_at,
        valid_until=assumption.valid_until,
        assumption_version=assumption.version,
        assumptions_note=assumption.source_reference,
        company_profile=payload.company_profile,
        diluted_shares=assumption.diluted_shares,
        scenarios=assumption.scenarios,
        required_margin=payload.required_margin,
        quantity=payload.lot_size,
        fees=payload.fees,
    )


def _validate_bars(
    payload: PriceValueBacktestRequest, bars: list[Any], trade_dates: list[date]
) -> dict[date, Any]:
    if not trade_dates or len(trade_dates) != len(set(trade_dates)):
        raise ValueError("execution calendar must contain unique open dates")
    if any(day < payload.start_date or day > payload.end_date for day in trade_dates):
        raise ValueError("execution calendar dates must be within backtest window")
    by_date = {}
    for bar in bars:
        if bar.symbol != payload.symbol:
            raise ValueError("market bar symbol must match the requested symbol")
        if bar.trade_date in by_date:
            raise ValueError("duplicate market bar")
        for field in ("open", "high", "low", "close"):
            value = getattr(bar, field, None)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ValueError("market OHLC prices must be positive finite Decimals")
        if not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("market OHLC prices must be internally consistent")
        if (
            not isinstance(bar.volume, Decimal)
            or not bar.volume.is_finite()
            or bar.volume < 0
        ):
            raise ValueError("market volume must be a non-negative finite Decimal")
        for field in ("limit_up", "limit_down"):
            value = getattr(bar, field, None)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise ValueError("market price limits must be positive finite Decimals")
        if (
            getattr(bar, "limit_up", None) is not None
            and getattr(bar, "limit_down", None) is not None
            and bar.limit_down >= bar.limit_up
        ):
            raise ValueError("market lower price limit must be below upper price limit")
        by_date[bar.trade_date] = bar
    if set(by_date) != set(trade_dates):
        raise ValueError("complete trusted bars must match every execution calendar date")
    return by_date


def _execution_blocker(bar: Any, side: str, fill_price: Decimal) -> str | None:
    if getattr(bar, "is_suspended", False):
        return "suspended"
    if getattr(bar, "limit_up", None) is None or getattr(bar, "limit_down", None) is None:
        return "price_limits_missing"
    if side == "buy" and bar.open >= bar.limit_up:
        return "limit_up"
    if side == "sell" and bar.open <= bar.limit_down:
        return "limit_down"
    if not bar.limit_down <= fill_price <= bar.limit_up or fill_price <= 0:
        return "fill_outside_price_limits"
    return None


def _affordable_quantity(
    *, quote: Decimal, budget: Decimal, capacity: int, lot_size: int, fees: AShareCostFeeModel
) -> int:
    """Find the largest affordable whole lot using actual order fees."""
    lower, upper = 0, min(capacity // lot_size, int(budget / quote) // lot_size)
    while lower < upper:
        middle = (lower + upper + 1) // 2
        quantity = middle * lot_size
        fill = fees.apply(reference_price=quote, quantity=quantity, side="buy")
        if quantity * fill.fill_price + fill.total_fee <= budget:
            lower = middle
        else:
            upper = middle - 1
    return lower * lot_size


def run_price_value_backtest(
    payload: PriceValueBacktestRequest, bars: list[Any], trade_dates: list[date]
) -> tuple[BacktestResult, list[PriceValueDecision]]:
    """Replay closes and subsequent opens without accessing future observations.

    Input bar provenance, security eligibility and corporate-action rejection
    belong to the service. User-supplied assumption times remain unverified.
    """
    with localcontext() as context:
        context.prec = 80
        return _run(payload, bars, trade_dates)


def _run(
    payload: PriceValueBacktestRequest, bars: list[Any], trade_dates: list[date]
) -> tuple[BacktestResult, list[PriceValueDecision]]:
    by_date = _validate_bars(payload, bars, trade_dates)
    timeline = sorted(trade_dates)
    assumptions = sorted(payload.assumptions, key=lambda item: item.available_at)
    valuations = {
        item.version: calculate_valuation(_valuation_inputs(payload, item))
        for item in assumptions
    }
    fees = AShareCostFeeModel(
        commission_rate=payload.fees.commission_rate,
        min_commission=payload.fees.min_commission,
        stamp_duty_rate=payload.sell_stamp_duty_rate,
        transfer_fee_rate=payload.fees.transfer_fee_rate,
        exchange_fee_rate=payload.fees.exchange_fee_rate,
        slippage_bps=payload.fees.slippage_bps,
    )
    cash = payload.initial_cash
    quantity = 0
    pending_buy: _Intent | None = None
    pending_exit: _Intent | None = None
    trades: list[BacktestTrade] = []
    positions: list[PositionSnapshot] = []
    equity: list[EquitySnapshot] = []
    decisions: list[PriceValueDecision] = []

    def latest(cutoff: datetime) -> PriceValueAssumption | None:
        return next((item for item in reversed(assumptions) if item.available_at <= cutoff), None)

    def record(
        day: date,
        phase: str,
        action: str,
        reason: str,
        assumption: PriceValueAssumption | None = None,
        signal_date: date | None = None,
    ) -> None:
        decisions.append(
            PriceValueDecision(
                trade_date=day,
                phase=phase,
                action=action,
                reason=reason,
                assumption_version=assumption.version if assumption else None,
                signal_date=signal_date,
            )
        )

    for index, day in enumerate(timeline):
        bar = by_date[day]
        open_at = datetime.combine(day, time(9, 30), tzinfo=SHANGHAI)
        close_at = datetime.combine(day, time(16), tzinfo=SHANGHAI)
        # Capacity is known before execution. Today's eventual volume never
        # determines a quantity at today's opening auction.
        prior_volume = by_date[timeline[index - 1]].volume if index else Decimal(0)
        capacity = int(prior_volume * payload.max_participation_rate) // payload.lot_size
        capacity *= payload.lot_size

        intent = pending_exit or pending_buy
        if intent is not None:
            is_buy = pending_exit is None
            side = "buy" if is_buy else "sell"
            reason = None
            current = latest(open_at)
            if is_buy:
                if intent.assumption.valid_until < open_at:
                    reason = "assumption_expired_before_open"
                elif current is None or current.version != intent.assumption.version:
                    reason = "assumption_revised_before_open"
                if reason is not None:
                    record(day, "open", "cancelled", reason, intent.assumption, intent.signal_date)
                    pending_buy = None
            if reason is None:
                fill_quantity = (
                    _affordable_quantity(
                        quote=bar.open,
                        budget=cash * payload.position_weight,
                        capacity=capacity,
                        lot_size=payload.lot_size,
                        fees=fees,
                    )
                    if is_buy
                    else min(quantity, capacity)
                )
                fill = fees.apply(reference_price=bar.open, quantity=fill_quantity, side=side)
                reason = _execution_blocker(bar, side, fill.fill_price)
                if reason is None and fill_quantity == 0:
                    reason = "prior_volume_capacity" if capacity == 0 else "insufficient_budget"
                if reason is None and is_buy:
                    assert intent.ceiling is not None
                    if fill.fill_price + fill.total_fee / fill_quantity > intent.ceiling:
                        reason = "all_in_price_above_ceiling"
                if (
                    reason is None and not is_buy
                    and cash + fill_quantity * fill.fill_price < fill.total_fee
                ):
                    reason = "insufficient_cash_for_exit_fees"
                if reason is not None:
                    record(day, "open", "unfilled", reason, intent.assumption, intent.signal_date)
                else:
                    amount = fill_quantity * fill.fill_price
                    cash += -amount - fill.total_fee if is_buy else amount - fill.total_fee
                    quantity += fill_quantity if is_buy else -fill_quantity
                    trades.append(
                        BacktestTrade(
                            signal_date=intent.signal_date,
                            trade_date=day,
                            symbol=payload.symbol,
                            side=side,
                            quantity=fill_quantity,
                            reference_price=bar.open,
                            fill_price=fill.fill_price,
                            commission=fill.total_fee,
                            cash_after=cash,
                        )
                    )
                    record(
                        day, "open", side,
                        "entry_filled" if is_buy
                        else "exit_filled" if quantity == 0 else "exit_partial",
                        intent.assumption, intent.signal_date,
                    )
                    if not is_buy and quantity == 0:
                        pending_exit = None
                # Entry opportunities last one opening only; any retry must
                # pass a fresh close evaluation. Exit intent persists until flat.
                pending_buy = None

        market_value = quantity * bar.close
        equity.append(EquitySnapshot(day, cash, market_value, cash + market_value))
        if quantity:
            positions.append(
                PositionSnapshot(day, payload.symbol, quantity, bar.close, market_value)
            )

        assumption = latest(close_at)
        if pending_exit is not None:
            record(
                day, "close", "hold", "exit_pending", pending_exit.assumption,
                pending_exit.signal_date,
            )
            continue
        # A thesis-break event remains a risk event after its valuation quote
        # expires. Ordinary price assumptions expire without forcing a sale.
        if quantity and assumption is not None and assumption.thesis_broken:
            if index < len(timeline) - 1:
                pending_exit = _Intent(day, assumption)
                record(day, "close", "sell_signal", "thesis_broken", assumption, day)
            else:
                record(day, "close", "review", "thesis_broken_at_window_end", assumption)
            continue
        if assumption is None or assumption.valid_until < close_at:
            record(
                day, "close", "review" if quantity else "wait",
                "assumption_missing" if assumption is None else "assumption_expired", assumption,
            )
            continue
        valuation = valuations[assumption.version]
        exit_reason = (
            "thesis_broken" if assumption.thesis_broken
            else "base_value_reached" if bar.close >= valuation["base"] else None
        )
        if quantity:
            if exit_reason is not None and index < len(timeline) - 1:
                pending_exit = _Intent(day, assumption)
                record(day, "close", "sell_signal", exit_reason, assumption, day)
            else:
                record(
                    day, "close", "review" if exit_reason else "hold",
                    "end_of_window" if exit_reason else "existing_position_no_top_up", assumption,
                )
            continue
        if assumption.thesis_broken:
            record(day, "close", "wait", "thesis_broken", assumption)
            continue
        if index == len(timeline) - 1:
            record(day, "close", "wait", "end_of_window", assumption)
            continue
        if getattr(bar, "is_suspended", False):
            record(day, "close", "wait", "suspended", assumption)
            continue
        next_capacity = int(bar.volume * payload.max_participation_rate) // payload.lot_size
        quote_quantity = _affordable_quantity(
            quote=bar.close, budget=cash * payload.position_weight,
            capacity=next_capacity * payload.lot_size, lot_size=payload.lot_size, fees=fees,
        )
        if not quote_quantity:
            record(day, "close", "wait", "insufficient_budget_or_capacity", assumption)
            continue
        estimate = fees.apply(reference_price=bar.close, quantity=quote_quantity, side="buy")
        ceiling = valuation["low"] * (Decimal(1) - payload.required_margin)
        if estimate.fill_price + estimate.total_fee / quote_quantity > ceiling:
            record(day, "close", "wait", "all_in_price_above_ceiling", assumption)
            continue
        pending_buy = _Intent(day, assumption, ceiling)
        record(day, "close", "buy_signal", "price_condition_met", assumption, day)

    final_equity = equity[-1].total_equity
    return BacktestResult(
        initial_cash=payload.initial_cash,
        final_equity=final_equity,
        total_return=final_equity / payload.initial_cash - Decimal(1),
        trades=trades,
        positions=positions,
        equity_curve=equity,
    ), decisions
