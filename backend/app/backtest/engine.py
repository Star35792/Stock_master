from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

ENGINE_VERSION = "daily_event_v2"


@dataclass(frozen=True)
class TargetWeightSignal:
    signal_date: date
    symbol: str
    target_weight: Decimal


@dataclass(frozen=True)
class BacktestTrade:
    signal_date: date
    trade_date: date
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    fill_price: Decimal
    commission: Decimal
    cash_after: Decimal


@dataclass(frozen=True)
class PositionSnapshot:
    trade_date: date
    symbol: str
    quantity: int
    close: Decimal
    market_value: Decimal


@dataclass(frozen=True)
class EquitySnapshot:
    trade_date: date
    cash: Decimal
    market_value: Decimal
    total_equity: Decimal


@dataclass(frozen=True)
class BacktestResult:
    initial_cash: Decimal
    final_equity: Decimal
    total_return: Decimal
    trades: list[BacktestTrade]
    positions: list[PositionSnapshot]
    equity_curve: list[EquitySnapshot]


class DailyEventBacktestEngine:
    def __init__(
        self,
        *,
        initial_cash: Decimal,
        commission_rate: Decimal = Decimal("0.0003"),
        slippage_bps: Decimal = Decimal("2"),
        lot_size: int = 100,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if commission_rate < 0:
            raise ValueError("commission_rate must be non-negative")
        if slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        self.initial_cash = initial_cash
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps
        self.lot_size = lot_size

    def run(
        self,
        *,
        bars: list[Any],
        signals: list[TargetWeightSignal],
        execution_calendar: dict[str, list[date]],
    ) -> BacktestResult:
        bars_by_date: dict[date, dict[str, Any]] = defaultdict(dict)
        for bar in bars:
            if bar.symbol in bars_by_date[bar.trade_date]:
                raise ValueError(
                    "duplicate market bar: "
                    f"{bar.symbol} {bar.trade_date.isoformat()}"
                )
            bars_by_date[bar.trade_date][bar.symbol] = bar
        signals_by_date: dict[date, list[TargetWeightSignal]] = defaultdict(list)
        seen_signals: set[tuple[date, str]] = set()
        for signal in signals:
            if signal.target_weight < 0 or signal.target_weight > 1:
                raise ValueError("target_weight must be between 0 and 1")
            signal_key = (signal.signal_date, signal.symbol)
            if signal_key in seen_signals:
                raise ValueError(
                    "duplicate target signal: "
                    f"{signal.symbol} {signal.signal_date.isoformat()}"
                )
            seen_signals.add(signal_key)
            signals_by_date[signal.signal_date].append(signal)
        for trade_date, daily_signals in signals_by_date.items():
            total_weight = sum(
                (signal.target_weight for signal in daily_signals),
                Decimal(),
            )
            if total_weight > 1:
                raise ValueError(f"target weights exceed 1 on {trade_date.isoformat()}")

        missing_calendars = sorted(
            {signal.symbol for signal in signals} - set(execution_calendar)
        )
        if missing_calendars:
            raise ValueError(
                "execution calendar is required for every signal symbol: "
                + ", ".join(missing_calendars)
            )
        empty_calendars = sorted(
            symbol for symbol, days in execution_calendar.items() if not days
        )
        if empty_calendars:
            raise ValueError(
                "execution calendar must contain open dates: "
                + ", ".join(empty_calendars)
            )
        calendars = {
            symbol: tuple(sorted(set(days)))
            for symbol, days in execution_calendar.items()
        }
        timeline = sorted(
            set(bars_by_date).union(
                day for days in calendars.values() for day in days
            )
        )

        cash = self.initial_cash
        quantities: dict[str, int] = defaultdict(int)
        last_close: dict[str, Decimal] = {}
        pending_targets: dict[str, tuple[date, Decimal, date]] = {}
        trades: list[BacktestTrade] = []
        positions: list[PositionSnapshot] = []
        equity_curve: list[EquitySnapshot] = []

        for trade_date in timeline:
            daily_bars = bars_by_date[trade_date]
            open_prices = {
                symbol: bar.open for symbol, bar in daily_bars.items()
            }
            opening_equity = cash + sum(
                Decimal(quantity)
                * open_prices.get(symbol, last_close.get(symbol, Decimal()))
                for symbol, quantity in quantities.items()
            )
            executable = {
                symbol: target
                for symbol, target in pending_targets.items()
                if target[2] == trade_date
            }
            missing_execution_bars = sorted(set(executable) - set(daily_bars))
            if missing_execution_bars:
                raise ValueError(
                    "missing trusted execution bar on scheduled trade date "
                    f"{trade_date.isoformat()}: {', '.join(missing_execution_bars)}"
                )
            orders = []
            for symbol, (signal_date, target_weight, _) in executable.items():
                reference_price = daily_bars[symbol].open
                target_quantity = self._round_lot(
                    opening_equity * target_weight / reference_price
                )
                quantity_delta = target_quantity - quantities[symbol]
                if quantity_delta:
                    orders.append(
                        (quantity_delta > 0, symbol, signal_date, quantity_delta)
                    )
            orders.sort(key=lambda order: (order[0], order[1], order[2]))

            for is_buy, symbol, signal_date, quantity_delta in orders:
                reference_price = daily_bars[symbol].open
                if is_buy:
                    fill_price = self._apply_slippage(reference_price, side="buy")
                    quantity = self._affordable_quantity(
                        desired_quantity=quantity_delta,
                        fill_price=fill_price,
                        cash=cash,
                    )
                    if quantity == 0:
                        pending_targets.pop(symbol, None)
                        continue
                    commission = Decimal(quantity) * fill_price * self.commission_rate
                    cash -= Decimal(quantity) * fill_price + commission
                    quantities[symbol] += quantity
                    side = "buy"
                else:
                    quantity = min(abs(quantity_delta), quantities[symbol])
                    fill_price = self._apply_slippage(reference_price, side="sell")
                    commission = Decimal(quantity) * fill_price * self.commission_rate
                    cash += Decimal(quantity) * fill_price - commission
                    quantities[symbol] -= quantity
                    side = "sell"
                trades.append(
                    BacktestTrade(
                        signal_date=signal_date,
                        trade_date=trade_date,
                        symbol=symbol,
                        side=side,
                        quantity=quantity,
                        reference_price=reference_price,
                        fill_price=fill_price,
                        commission=commission,
                        cash_after=cash,
                    )
                )
                pending_targets.pop(symbol, None)

            for symbol, bar in daily_bars.items():
                last_close[symbol] = bar.close
            market_value = Decimal()
            for symbol in sorted(quantities):
                quantity = quantities[symbol]
                if quantity == 0 or symbol not in last_close:
                    continue
                value = Decimal(quantity) * last_close[symbol]
                market_value += value
                positions.append(
                    PositionSnapshot(
                        trade_date=trade_date,
                        symbol=symbol,
                        quantity=quantity,
                        close=last_close[symbol],
                        market_value=value,
                    )
                )
            total_equity = cash + market_value
            equity_curve.append(
                EquitySnapshot(
                    trade_date=trade_date,
                    cash=cash,
                    market_value=market_value,
                    total_equity=total_equity,
                )
            )

            for signal in signals_by_date.get(trade_date, []):
                scheduled_date = self._next_execution_date(
                    symbol=signal.symbol,
                    signal_date=signal.signal_date,
                    calendars=calendars,
                )
                if scheduled_date is None:
                    continue
                pending_targets[signal.symbol] = (
                    signal.signal_date,
                    signal.target_weight,
                    scheduled_date,
                )

        final_equity = (
            equity_curve[-1].total_equity if equity_curve else self.initial_cash
        )
        return BacktestResult(
            initial_cash=self.initial_cash,
            final_equity=final_equity,
            total_return=final_equity / self.initial_cash - Decimal(1),
            trades=trades,
            positions=positions,
            equity_curve=equity_curve,
        )

    def _apply_slippage(self, price: Decimal, *, side: str) -> Decimal:
        rate = self.slippage_bps / Decimal(10000)
        return price * (Decimal(1) + rate if side == "buy" else Decimal(1) - rate)

    def _affordable_quantity(
        self,
        *,
        desired_quantity: int,
        fill_price: Decimal,
        cash: Decimal,
    ) -> int:
        unit_cost = fill_price * (Decimal(1) + self.commission_rate)
        affordable = self._round_lot(cash / unit_cost)
        return min(desired_quantity, affordable)

    def _round_lot(self, quantity: Decimal) -> int:
        return int(quantity) // self.lot_size * self.lot_size

    @staticmethod
    def _next_execution_date(
        *,
        symbol: str,
        signal_date: date,
        calendars: dict[str, tuple[date, ...]],
    ) -> date | None:
        available_dates = calendars[symbol]
        index = bisect_right(available_dates, signal_date)
        return available_dates[index] if index < len(available_dates) else None
