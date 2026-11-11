from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.backtest.fees import (
    LINEAR_COMMISSION_V1,
    FeeModel,
    build_fee_model,
)
from app.backtest.tradability import AShareTradabilityPolicy
from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints

ENGINE_VERSION = "daily_event_v3"


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
        fee_model: FeeModel | None = None,
        tradability_policy: AShareTradabilityPolicy | None = None,
        portfolio_constraints: PortfolioConstraints | None = None,
        security_profiles: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        if lot_size <= 0:
            raise ValueError("lot_size must be positive")
        self.initial_cash = initial_cash
        self.lot_size = lot_size
        if fee_model is not None:
            self.fee_model = fee_model
        else:
            if commission_rate < 0:
                raise ValueError("commission_rate must be non-negative")
            if slippage_bps < 0:
                raise ValueError("slippage_bps must be non-negative")
            self.fee_model = build_fee_model(
                fee_model_version=LINEAR_COMMISSION_V1,
                commission_rate=commission_rate,
                slippage_bps=slippage_bps,
            )
        self.tradability_policy = tradability_policy or AShareTradabilityPolicy()
        # Optional same-source portfolio construction: when set, every
        # execution day's pending targets go through PortfolioBuilder so the
        # backtest honours the same symbol/industry/type/cash constraints as
        # the paper path. Profiles map symbol -> (security_type, industry).
        self.portfolio_constraints = portfolio_constraints
        self.security_profiles = security_profiles or {}
        self.portfolio_builder = PortfolioBuilder() if portfolio_constraints else None

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
            missing_held_bars = sorted(
                symbol for symbol, quantity in quantities.items()
                if quantity > 0
                and trade_date in calendars[symbol]
                and symbol not in daily_bars
            )
            if missing_held_bars:
                raise ValueError(
                    "missing trusted held-position bar on open date "
                    f"{trade_date.isoformat()}: {', '.join(missing_held_bars)}"
                )
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
            if self.portfolio_constraints is not None and executable:
                executable = self._apply_portfolio_constraints(
                    executable=executable,
                    daily_bars=daily_bars,
                    opening_equity=opening_equity,
                    cash=cash,
                    quantities=quantities,
                    last_close=last_close,
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
                bar = daily_bars[symbol]
                reference_price = bar.open
                side = "buy" if is_buy else "sell"
                tradability = self.tradability_policy.check(bar)
                if is_buy and not tradability.can_buy:
                    pending_targets.pop(symbol, None)
                    continue
                if not is_buy and not tradability.can_sell:
                    pending_targets.pop(symbol, None)
                    continue
                if is_buy:
                    quantity = self._affordable_quantity(
                        desired_quantity=quantity_delta,
                        reference_price=reference_price,
                        cash=cash,
                    )
                    if quantity == 0:
                        pending_targets.pop(symbol, None)
                        continue
                else:
                    quantity = min(abs(quantity_delta), quantities[symbol])

                fee_result = self.fee_model.apply(
                    reference_price=reference_price,
                    quantity=quantity,
                    side=side,
                )
                fill_price = fee_result.fill_price
                commission = fee_result.total_fee
                amount = Decimal(quantity) * fill_price
                if is_buy:
                    cash -= amount + commission
                    quantities[symbol] += quantity
                else:
                    cash += amount - commission
                    quantities[symbol] -= quantity
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

    def _apply_portfolio_constraints(
        self,
        *,
        executable: dict[str, tuple[date, Decimal, date]],
        daily_bars: dict[str, Any],
        opening_equity: Decimal,
        cash: Decimal,
        quantities: dict[str, int],
        last_close: dict[str, Decimal],
    ) -> dict[str, tuple[date, Decimal, date]]:
        """Re-weigh one execution day's targets through PortfolioBuilder.

        Held symbols not in the day's targets are passed with a zero
        requested weight so the builder sees the full book (and can scale the
        rest against the real total equity); held names missing both an
        execution-day bar and a last close cannot be valued and are skipped.
        """
        assets: list[PortfolioAsset] = []
        covered: set[str] = set()
        for symbol in sorted(set(executable) | {s for s, q in quantities.items() if q > 0}):
            bar = daily_bars.get(symbol)
            price = bar.open if bar is not None else last_close.get(symbol)
            if price is None or price <= 0:
                continue
            security_type, industry = self.security_profiles.get(symbol, ("stock", "unknown"))
            requested = (
                executable[symbol][1] if symbol in executable else Decimal()
            )
            assets.append(
                PortfolioAsset(
                    symbol=symbol,
                    security_type=security_type,
                    industry=industry,
                    price=price,
                    current_quantity=quantities.get(symbol, 0),
                    requested_weight=requested,
                    lot_size=self.lot_size,
                )
            )
            covered.add(symbol)
        if not assets:
            return executable
        assert self.portfolio_builder is not None
        plan = self.portfolio_builder.build(
            assets=assets,
            cash=cash,
            constraints=self.portfolio_constraints,
        )
        final_weights = {target.symbol: target.final_weight for target in plan.targets}
        constrained: dict[str, tuple[date, Decimal, date]] = {}
        for symbol, target in executable.items():
            if symbol not in covered or symbol not in final_weights:
                continue
            signal_date, _, scheduled = target
            # final_weight is derived from builder quantities; convert back to
            # a weight against the engine's opening equity so downstream lot
            # sizing and affordability stay on the engine's own accounting.
            constrained[symbol] = (
                signal_date,
                final_weights[symbol] if opening_equity > 0 else Decimal(),
                scheduled,
            )
        return constrained

    def _affordable_quantity(
        self,
        *,
        desired_quantity: int,
        reference_price: Decimal,
        cash: Decimal,
    ) -> int:
        # Supported fee models have monotonic order costs. Search whole lots
        # using the actual fill cost so minimum commissions are charged once.
        low, high = 0, desired_quantity // self.lot_size
        while low < high:
            candidate = (low + high + 1) // 2
            quantity = candidate * self.lot_size
            fee = self.fee_model.apply(
                reference_price=reference_price, quantity=quantity, side="buy",
            )
            if Decimal(quantity) * fee.fill_price + fee.total_fee <= cash:
                low = candidate
            else:
                high = candidate - 1
        return low * self.lot_size

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
