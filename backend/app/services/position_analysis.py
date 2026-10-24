"""Read-only analysis for paper accounts: holdings and equity performance.

Same trusted-price discipline as the valuation engine (unadjusted close,
trusted/manual_fixed, non-suspect). Holdings come from ``trade.paper_position``
plus master data; performance is derived from the append-only equity snapshot
series without recomputing any audited value.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

from sqlalchemy.orm import Session

from app.repositories.market import DailyBarRepository, SecurityRepository
from app.repositories.trade import PaperEquitySnapshotRepository, PaperTradingRepository
from app.schemas.position_analysis import (
    PaperPerformancePointRead,
    PaperPerformanceRead,
    PaperPerformanceSummaryRead,
    PaperPositionRowRead,
    PaperPositionsRead,
)
from app.schemas.position_book import PositionConcentrationRead
from app.services.paper_equity import PaperAccountNotFound
from app.services.position_metrics import (
    SymbolValuation,
    compute_concentration,
    finalize_valuation,
    group_industry_exposure,
    quantize_money,
    quantize_ratio,
)

TRADING_DAYS_PER_YEAR = Decimal("252")


class PaperEquitySnapshotNotFound(LookupError):
    pass


class PaperPositionAnalysisService:
    def __init__(
        self,
        *,
        paper_repository: PaperTradingRepository | None = None,
        equity_repository: PaperEquitySnapshotRepository | None = None,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
    ) -> None:
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.equity_repository = equity_repository or PaperEquitySnapshotRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()

    def get_positions(self, db: Session, *, account_id: int) -> PaperPositionsRead:
        account = self.paper_repository.get_account(db, account_id=account_id)
        if account is None:
            raise PaperAccountNotFound("paper account not found")
        positions = self.paper_repository.list_positions(db, account_id=account_id)
        symbols = {position.symbol for position in positions}
        securities = {
            security.symbol: security
            for security in self.security_repository.get_many(db, symbols=symbols)
            if symbols
        }
        bars = {
            bar.symbol: bar
            for bar in self.daily_bar_repository.list_latest_consumable_by_symbols(
                db, symbols=symbols, adjust_type="none"
            )
            if symbols
        }

        cash = Decimal(account.cash_balance)
        holdings: list[PaperPositionRowRead] = []
        valuations: dict[str, SymbolValuation] = {}
        price_as_of: date | None = None
        for position in positions:
            bar = bars.get(position.symbol)
            valuation = SymbolValuation(
                symbol=position.symbol,
                quantity=position.quantity,
                cost_basis=quantize_money(
                    Decimal(position.quantity) * Decimal(position.average_cost)
                ),
                average_cost=Decimal(position.average_cost),
            )
            finalize_valuation(
                valuation,
                price=bar.close if bar else None,
                price_as_of=bar.trade_date if bar else None,
                price_quality_status=bar.quality_status if bar else None,
            )
            if bar is not None and (price_as_of is None or bar.trade_date > price_as_of):
                price_as_of = bar.trade_date
            valuations[position.symbol] = valuation

        positions_market_value = sum(
            (valuation.market_value for valuation in valuations.values()), Decimal()
        )
        total_equity = quantize_money(cash + positions_market_value)
        for symbol, valuation in valuations.items():
            security = securities.get(symbol)
            weight = (
                valuation.market_value / total_equity if total_equity > 0 else Decimal()
            )
            holdings.append(
                PaperPositionRowRead(
                    symbol=symbol,
                    name=security.name if security else None,
                    industry=security.industry if security else None,
                    security_type=security.security_type if security else None,
                    exchange=security.exchange if security else None,
                    quantity=valuation.quantity,
                    average_cost=valuation.average_cost,
                    price=valuation.price,
                    price_as_of=valuation.price_as_of,
                    price_quality_status=valuation.price_quality_status,
                    price_stale=valuation.price_stale,
                    market_value=valuation.market_value,
                    unrealized_pnl=valuation.unrealized_pnl,
                    unrealized_pnl_pct=valuation.unrealized_pnl_pct,
                    weight=quantize_ratio(weight),
                )
            )
        holdings.sort(key=lambda row: row.market_value, reverse=True)

        hhi, top5, max_weight = compute_concentration([row.weight for row in holdings])
        exposure_rows = group_industry_exposure(
            [
                (
                    securities[symbol].industry if symbol in securities else None,
                    valuation.market_value,
                )
                for symbol, valuation in valuations.items()
            ],
            total_equity=total_equity,
        )
        return PaperPositionsRead(
            account_id=account.id,
            price_as_of=price_as_of,
            price_stale=any(valuation.price_stale for valuation in valuations.values()),
            cash_balance=quantize_money(cash),
            positions_market_value=quantize_money(positions_market_value),
            total_equity=total_equity,
            concentration=PositionConcentrationRead(
                position_count=len(holdings),
                hhi=hhi,
                top5_weight=top5,
                max_symbol_weight=max_weight,
            ),
            industry_exposure=exposure_rows,
            holdings=holdings,
        )

    def get_performance(self, db: Session, *, account_id: int) -> PaperPerformanceRead:
        account = self.paper_repository.get_account(db, account_id=account_id)
        if account is None:
            raise PaperAccountNotFound("paper account not found")
        snapshots = self.equity_repository.list_snapshots(db, account_id=account_id)
        curve = [
            PaperPerformancePointRead(
                valuation_date=snapshot.valuation_date,
                cash_balance=quantize_money(Decimal(snapshot.cash_balance)),
                positions_market_value=quantize_money(Decimal(snapshot.positions_market_value)),
                total_equity=quantize_money(Decimal(snapshot.total_equity)),
                drawdown=quantize_ratio(Decimal(snapshot.drawdown)),
                history_status=snapshot.history_status,
            )
            for snapshot in snapshots
        ]
        return PaperPerformanceRead(
            account_id=account.id,
            summary=self._summarize(snapshots),
            curve=curve,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _summarize(snapshots) -> PaperPerformanceSummaryRead:
        if not snapshots:
            return PaperPerformanceSummaryRead(
                observation_count=0,
                monitoring_start_date=None,
                first_valuation_date=None,
                last_valuation_date=None,
                latest_total_equity=Decimal(),
                high_watermark=Decimal(),
                max_drawdown=Decimal(),
                cumulative_return=None,
                annualized_return=None,
                approximate=False,
                daily_volatility=None,
                volatility_approximate=False,
            )
        first, last = snapshots[0], snapshots[-1]
        first_equity = Decimal(first.total_equity)
        last_equity = Decimal(last.total_equity)
        cumulative = (
            quantize_ratio(last_equity / first_equity - Decimal(1))
            if first_equity > 0
            else None
        )
        annualized = None
        approximate = any(snapshot.history_status == "gap" for snapshot in snapshots)
        if cumulative is not None and first_equity > 0:
            days = Decimal((last.valuation_date - first.valuation_date).days)
            # Annualizing from a handful of days produces absurd extrapolations;
            # require at least a month of observation before extrapolating.
            if days >= 30:
                with localcontext() as ctx:
                    ctx.prec = 28
                    growth = (last_equity / first_equity) ** (Decimal(365) / days)
                annualized = quantize_ratio(growth - Decimal(1))

        returns = []
        for previous, current in zip(snapshots, snapshots[1:], strict=False):
            if (current.valuation_date - previous.valuation_date).days != 1:
                continue
            base = Decimal(previous.total_equity)
            if base > 0:
                returns.append(Decimal(current.total_equity) / base - Decimal(1))
        daily_volatility = (
            _annualized_daily_stdev(returns) if len(returns) >= 2 else None
        )

        return PaperPerformanceSummaryRead(
            observation_count=len(snapshots),
            monitoring_start_date=first.monitoring_start_date,
            first_valuation_date=first.valuation_date,
            last_valuation_date=last.valuation_date,
            latest_total_equity=quantize_money(last_equity),
            high_watermark=quantize_money(Decimal(last.high_watermark)),
            max_drawdown=quantize_ratio(
                min((Decimal(snapshot.drawdown) for snapshot in snapshots), default=Decimal())
            ),
            cumulative_return=cumulative,
            annualized_return=annualized,
            approximate=approximate,
            daily_volatility=daily_volatility,
            volatility_approximate=approximate,
        )


def _annualized_daily_stdev(values: list[Decimal]) -> Decimal:
    mean = sum(values, Decimal()) / Decimal(len(values))
    variance = sum(((value - mean) ** 2 for value in values), Decimal()) / Decimal(
        len(values) - 1
    )
    with localcontext() as ctx:
        ctx.prec = 28
        return (variance.sqrt() * TRADING_DAYS_PER_YEAR.sqrt()).quantize(
            Decimal("0.0000000001"), rounding=ROUND_HALF_UP
        )
