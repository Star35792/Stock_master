from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from typing import Any


@dataclass(frozen=True)
class PerformanceReport:
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal | None
    calmar_ratio: Decimal | None
    daily_win_rate: Decimal
    profit_loss_ratio: Decimal | None
    turnover_rate: Decimal
    total_commission: Decimal
    symbol_contributions: dict[str, Decimal]
    industry_exposure: dict[str, Decimal]
    style_exposure: dict[str, Decimal]


@dataclass(frozen=True)
class EquityPerformance:
    total_return: Decimal
    annualized_return: Decimal
    maximum_drawdown: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal | None


class BacktestPerformanceAnalyzer:
    def analyze_equity(
        self, *, initial_cash: Decimal, equities: list[Decimal]
    ) -> EquityPerformance:
        if not equities:
            raise ValueError("backtest equity curve is empty")
        if not initial_cash.is_finite() or initial_cash <= 0 or any(
            not equity.is_finite() or equity <= 0 for equity in equities
        ):
            raise ValueError("performance requires positive initial cash and equity")
        daily_returns = [equities[0] / initial_cash - Decimal(1)]
        daily_returns.extend(
            equities[index] / equities[index - 1] - Decimal(1)
            for index in range(1, len(equities))
        )
        total_return = equities[-1] / initial_cash - Decimal(1)
        daily_mean = self._mean(daily_returns) or Decimal()
        daily_std = self._standard_deviation(daily_returns) or Decimal()
        annualizer = Decimal(str(sqrt(252)))
        return EquityPerformance(
            total_return=total_return,
            annualized_return=self._annualized_return(total_return, len(daily_returns)),
            maximum_drawdown=self._maximum_drawdown([initial_cash, *equities]),
            annualized_volatility=daily_std * annualizer,
            sharpe_ratio=daily_mean / daily_std * annualizer if daily_std else None,
        )

    def analyze(
        self,
        *,
        run: Any,
        equity_rows: list[Any],
        trade_rows: list[Any],
        position_rows: list[Any],
        industries: dict[str, str],
        style_values: dict[str, dict[str, Decimal]] | None = None,
    ) -> PerformanceReport:
        equity_rows = sorted(equity_rows, key=lambda row: row.trade_date)
        equities = [row.total_equity for row in equity_rows]
        equity = self.analyze_equity(initial_cash=run.initial_cash, equities=equities)
        daily_returns = [equities[0] / run.initial_cash - Decimal(1)]
        daily_returns.extend(
            equities[index] / equities[index - 1] - Decimal(1)
            for index in range(1, len(equities))
        )
        calmar_ratio = (
            equity.annualized_return / abs(equity.maximum_drawdown)
            if equity.maximum_drawdown else None
        )
        positive_returns = [value for value in daily_returns if value > 0]
        negative_returns = [value for value in daily_returns if value < 0]
        daily_win_rate = (
            Decimal(len(positive_returns)) / Decimal(len(daily_returns))
            if daily_returns
            else Decimal()
        )
        average_gain = self._mean(positive_returns)
        average_loss = self._mean([abs(value) for value in negative_returns])
        profit_loss_ratio = (
            average_gain / average_loss
            if average_gain is not None and average_loss not in {None, Decimal()}
            else None
        )
        average_equity = self._mean(equities) or run.initial_cash
        traded_notional = sum(
            (Decimal(row.quantity) * row.fill_price for row in trade_rows),
            Decimal(),
        )
        turnover_rate = traded_notional / average_equity if average_equity else Decimal()
        total_commission = sum((row.commission for row in trade_rows), Decimal())
        symbol_contributions = self._symbol_contributions(
            initial_cash=run.initial_cash,
            trade_rows=trade_rows,
            position_rows=position_rows,
            final_date=equity_rows[-1].trade_date,
        )
        industry_exposure, style_exposure = self._exposures(
            final_equity=equities[-1],
            position_rows=position_rows,
            final_date=equity_rows[-1].trade_date,
            industries=industries,
            style_values=style_values or {},
        )
        return PerformanceReport(
            total_return=equity.total_return,
            annualized_return=equity.annualized_return,
            maximum_drawdown=equity.maximum_drawdown,
            annualized_volatility=equity.annualized_volatility,
            sharpe_ratio=equity.sharpe_ratio,
            calmar_ratio=calmar_ratio,
            daily_win_rate=daily_win_rate,
            profit_loss_ratio=profit_loss_ratio,
            turnover_rate=turnover_rate,
            total_commission=total_commission,
            symbol_contributions=symbol_contributions,
            industry_exposure=industry_exposure,
            style_exposure=style_exposure,
        )

    @staticmethod
    def _annualized_return(total_return: Decimal, periods: int) -> Decimal:
        if periods <= 0 or total_return <= Decimal(-1):
            return Decimal()
        return Decimal(str(float(Decimal(1) + total_return) ** (252 / periods) - 1))

    @staticmethod
    def _maximum_drawdown(equities: list[Decimal]) -> Decimal:
        peak = equities[0]
        maximum_drawdown = Decimal()
        for equity in equities:
            peak = max(peak, equity)
            drawdown = equity / peak - Decimal(1) if peak else Decimal()
            maximum_drawdown = min(maximum_drawdown, drawdown)
        return maximum_drawdown

    def _symbol_contributions(
        self,
        *,
        initial_cash: Decimal,
        trade_rows: list[Any],
        position_rows: list[Any],
        final_date: date,
    ) -> dict[str, Decimal]:
        pnl_by_symbol: dict[str, Decimal] = {}
        for trade in trade_rows:
            notional = Decimal(trade.quantity) * trade.fill_price
            cash_flow = (
                -notional - trade.commission
                if trade.side == "buy"
                else notional - trade.commission
            )
            pnl_by_symbol[trade.symbol] = pnl_by_symbol.get(trade.symbol, Decimal()) + cash_flow
        latest_positions = self._latest_positions(position_rows, final_date=final_date)
        for symbol, position in latest_positions.items():
            pnl_by_symbol[symbol] = (
                pnl_by_symbol.get(symbol, Decimal()) + position.market_value
            )
        return {
            symbol: pnl / initial_cash for symbol, pnl in sorted(pnl_by_symbol.items())
        }

    def _exposures(
        self,
        *,
        final_equity: Decimal,
        position_rows: list[Any],
        final_date: date,
        industries: dict[str, str],
        style_values: dict[str, dict[str, Decimal]],
    ) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
        latest_positions = self._latest_positions(position_rows, final_date=final_date)
        industry_exposure: dict[str, Decimal] = {}
        for symbol, position in latest_positions.items():
            industry = industries.get(symbol, "UNKNOWN")
            industry_exposure[industry] = (
                industry_exposure.get(industry, Decimal())
                + position.market_value / final_equity
            )
        style_exposure = {
            style_name: sum(
                (
                    position.market_value / final_equity * values.get(symbol, Decimal())
                    for symbol, position in latest_positions.items()
                ),
                Decimal(),
            )
            for style_name, values in style_values.items()
        }
        return industry_exposure, style_exposure

    @staticmethod
    def _latest_positions(
        position_rows: list[Any],
        *,
        final_date: date,
    ) -> dict[str, Any]:
        return {
            row.symbol: row for row in position_rows if row.trade_date == final_date
        }

    @staticmethod
    def _mean(values: list[Decimal]) -> Decimal | None:
        return sum(values, Decimal()) / Decimal(len(values)) if values else None

    def _standard_deviation(self, values: list[Decimal]) -> Decimal | None:
        mean = self._mean(values)
        if mean is None:
            return None
        variance = sum(((value - mean) ** 2 for value in values), Decimal()) / Decimal(
            len(values)
        )
        return variance.sqrt()
