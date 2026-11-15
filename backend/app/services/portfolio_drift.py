"""Rebalance drift between a portfolio plan and actual holdings.

Read-only diagnostic: it compares a plan's constrained target weights with
the weights implied by a real-position ledger book (SQLite) or a paper
account (DuckDB), valued at the latest trusted market prices. It suggests a
direction and a lot-sized target quantity; it never creates orders or
approvals.
"""

from decimal import ROUND_FLOOR, Decimal

from sqlalchemy.orm import Session

from app.repositories.market import DailyBarRepository, SecurityRepository
from app.repositories.portfolio import PortfolioPlanRepository
from app.repositories.positions import PositionBookRepository
from app.repositories.trade import PaperTradingRepository
from app.schemas.portfolio import (
    PortfolioDriftRowRead,
    PortfolioDriftSummaryRead,
    PortfolioPlanDriftRead,
)
from app.services.portfolio_readiness import PaperAccountNotFound, PortfolioPlanNotFound
from app.services.position_book import PositionBookNotFound, PositionBookService
from app.services.position_metrics import (
    SymbolValuation,
    build_cost_basis_valuations,
    quantize_money,
    quantize_ratio,
)

DEFAULT_DRIFT_THRESHOLD = Decimal("0.02")
LOT_SIZE = 100


class PortfolioDriftValidationError(ValueError):
    """Invalid drift request with optional machine-readable diagnostics."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        symbols: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.symbols = symbols


class PortfolioDriftService:
    def __init__(
        self,
        *,
        plan_repository: PortfolioPlanRepository | None = None,
        book_repository: PositionBookRepository | None = None,
        paper_repository: PaperTradingRepository | None = None,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
        position_book_service: PositionBookService | None = None,
    ) -> None:
        self.plan_repository = plan_repository or PortfolioPlanRepository()
        self.book_repository = book_repository or PositionBookRepository()
        self.paper_repository = paper_repository or PaperTradingRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.position_book_service = position_book_service or PositionBookService(
            book_repository=self.book_repository,
            security_repository=self.security_repository,
            daily_bar_repository=self.daily_bar_repository,
        )

    def get_drift(
        self,
        db: Session,
        positions_db: Session,
        *,
        plan_id: int,
        position_book_id: int | None = None,
        paper_account_id: int | None = None,
        threshold: Decimal = DEFAULT_DRIFT_THRESHOLD,
    ) -> PortfolioPlanDriftRead:
        if (position_book_id is None) == (paper_account_id is None):
            raise PortfolioDriftValidationError(
                "exactly one of position_book_id or paper_account_id is required"
            )
        if threshold <= 0 or threshold > 1:
            raise PortfolioDriftValidationError("threshold must be within (0, 1]")
        plan, targets = self.plan_repository.get_with_targets(db, plan_id=plan_id)
        if plan is None:
            raise PortfolioPlanNotFound("portfolio plan not found")

        if position_book_id is not None:
            try:
                book, cash, valuations, price_as_of = self.position_book_service.get_valuation(
                    positions_db, db, book_id=position_book_id
                )
            except PositionBookNotFound as exc:
                raise PortfolioDriftValidationError(str(exc)) from exc
            source, source_id = "position_book", book.id
        else:
            account = self.paper_repository.get_account(db, account_id=paper_account_id)
            if account is None:
                raise PaperAccountNotFound("paper account not found")
            cash, valuations, price_as_of = self._paper_valuations(
                db, account_id=paper_account_id
            )
            source, source_id = "paper_account", account.id

        missing_price_symbols = sorted(
            {
                symbol
                for symbol, valuation in valuations.items()
                if valuation.quantity > 0
                and (valuation.price is None or valuation.price_stale)
            }
        )
        if missing_price_symbols:
            raise PortfolioDriftValidationError(
                "portfolio drift was not calculated because non-zero holdings "
                "lack trusted prices: " + ", ".join(missing_price_symbols),
                code="portfolio_drift_missing_trusted_prices",
                symbols=tuple(missing_price_symbols),
            )

        positions_market_value = sum(
            (valuation.market_value for valuation in valuations.values()), Decimal()
        )
        total_equity = quantize_money(cash + positions_market_value)
        weights = {
            symbol: (
                quantize_ratio(valuation.market_value / total_equity)
                if total_equity > 0
                else Decimal()
            )
            for symbol, valuation in valuations.items()
        }
        cash_weight = (
            quantize_ratio(cash / total_equity) if total_equity > 0 else Decimal()
        )
        plan_total = Decimal(plan.total_equity)
        cash_target_weight = (
            quantize_ratio(Decimal(plan.target_cash) / plan_total)
            if plan_total > 0
            else Decimal()
        )

        rows: list[PortfolioDriftRowRead] = []
        for target in targets:
            target_weight = Decimal(target.final_weight)
            current_weight = weights.get(target.symbol, Decimal())
            drift = current_weight - target_weight
            if drift <= -threshold:
                direction, direction_reason = "buy", "underweight beyond threshold"
            elif drift >= threshold:
                direction, direction_reason = "sell", "overweight beyond threshold"
            else:
                direction, direction_reason = "hold", "within drift threshold"
            reasons = [direction_reason]

            target_quantity: int | None = None
            valuation = valuations.get(target.symbol)
            price = valuation.price if valuation else None
            if price is not None and total_equity > 0:
                raw_quantity = target_weight * total_equity / price
                lots = (raw_quantity / LOT_SIZE).to_integral_value(rounding=ROUND_FLOOR)
                target_quantity = int(lots) * LOT_SIZE
                if target_quantity == 0:
                    reasons.append("target weight is below one round lot at this price")
            else:
                reasons.append("no trusted price for lot sizing")

            rows.append(
                PortfolioDriftRowRead(
                    symbol=target.symbol,
                    industry=target.industry,
                    security_type=target.security_type,
                    target_weight=quantize_ratio(target_weight),
                    current_weight=quantize_ratio(current_weight),
                    drift=quantize_ratio(drift),
                    direction=direction,
                    current_quantity=valuation.quantity if valuation else 0,
                    target_quantity=target_quantity,
                    reasons=reasons,
                )
            )

        summary = PortfolioDriftSummaryRead(
            max_abs_drift=quantize_ratio(
                max((abs(row.drift) for row in rows), default=Decimal())
            ),
            off_threshold_count=sum(1 for row in rows if row.direction != "hold"),
            cash_weight=cash_weight,
            cash_target_weight=cash_target_weight,
        )
        return PortfolioPlanDriftRead(
            plan_id=plan.id,
            source=source,
            source_id=source_id,
            source_name=(
                self.book_repository.get_book(positions_db, book_id=source_id).name
                if source == "position_book"
                else None
            ),
            threshold=quantize_ratio(threshold),
            price_as_of=price_as_of,
            total_equity=total_equity,
            summary=summary,
            rows=rows,
        )

    def _paper_valuations(
        self,
        db: Session,
        *,
        account_id: int,
    ) -> tuple[Decimal, dict[str, SymbolValuation], object | None]:
        positions = self.paper_repository.list_positions(db, account_id=account_id)
        symbols = {position.symbol for position in positions}
        bars = {
            bar.symbol: bar
            for bar in self.daily_bar_repository.list_latest_consumable_by_symbols(
                db, symbols=symbols, adjust_type="none"
            )
            if symbols
        }
        cash = Decimal(self.paper_repository.get_account(db, account_id=account_id).cash_balance)
        valuations, price_as_of = build_cost_basis_valuations(positions, bars)
        return cash, valuations, price_as_of
