"""Real-position ledger service.

The ledger records actual trades the user executed in an external brokerage
terminal and entered by hand. It never connects to a broker and never
generates orders. Trades are the only facts stored (SQLite); current
holdings, average cost and realized P&L are derived by replaying the ledger
in (trade_date, id) order. Valuation prices come from the DuckDB ``market``
trusted layer using the same discipline as the paper engine (unadjusted
close, trusted/manual_fixed, non-suspect).
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.positions import PositionBook, PositionTrade
from app.repositories.market import DailyBarRepository, SecurityRepository
from app.repositories.positions import PositionBookRepository
from app.schemas.position_book import (
    PositionBookCreateRequest,
    PositionBookHoldingsRead,
    PositionBookRead,
    PositionBookUpdateRequest,
    PositionConcentrationRead,
    PositionHoldingRead,
    PositionIndustryExposureRead,
    PositionTradeCreateRequest,
    PositionTradeRead,
)
from app.services.position_metrics import (
    COST_QUANTUM,
    SymbolValuation,
    compute_concentration,
    finalize_valuation,
    group_industry_exposure,
    quantize_money,
    quantize_ratio,
)

QUANT_SCALE = 10000  # one minor unit = 0.0001 CNY, matching MONEY_QUANTUM domains


class PositionBookNotFound(LookupError):
    pass


class PositionTradeNotFound(LookupError):
    pass


class PositionBookConflict(ValueError):
    pass


class PositionBookValidationError(ValueError):
    pass


def to_quants(value: Decimal) -> int:
    scaled = value * QUANT_SCALE
    if scaled != scaled.to_integral_value():
        raise PositionBookValidationError("money amounts support at most 4 decimal places")
    return int(scaled)


def from_quants(value: int) -> Decimal:
    return (Decimal(value) / Decimal(QUANT_SCALE)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


@dataclass
class LedgerPosition:
    symbol: str
    quantity: int = 0
    cost: Decimal = Decimal()
    realized_pnl: Decimal = Decimal()
    sequence_anomaly: bool = False
    extras: dict = field(default_factory=dict)


def replay_ledger(trades: list[PositionTrade]) -> dict[str, LedgerPosition]:
    """Replay trades in (trade_date, id) order into per-symbol positions.

    Average cost includes buy fees; sells consume cost at the rounded average
    (6dp, matching the paper engine's Numeric(18,6) cost semantics) and the
    difference is booked as realized P&L. Out-of-order entry can make the
    sequential replay sell more than held; that sets ``sequence_anomaly``
    instead of failing, and the proceeds are treated as fully realized.
    """
    positions: dict[str, LedgerPosition] = {}
    for trade in sorted(trades, key=lambda item: (item.trade_date, item.id)):
        pos = positions.setdefault(trade.symbol, LedgerPosition(symbol=trade.symbol))
        amount = from_quants(trade.price_quants) * Decimal(trade.quantity)
        proceeds = amount - from_quants(trade.fee_quants)
        if trade.side == "buy":
            pos.quantity += trade.quantity
            pos.cost += amount + from_quants(trade.fee_quants)
            continue

        if trade.quantity > pos.quantity:
            pos.sequence_anomaly = True
        sell_qty = min(trade.quantity, pos.quantity)
        if sell_qty > 0 and pos.cost > 0:
            average = (pos.cost / Decimal(pos.quantity)).quantize(
                COST_QUANTUM, rounding=ROUND_HALF_UP
            )
            cost_share = min(average * Decimal(sell_qty), pos.cost)
            pos.realized_pnl += proceeds - cost_share
            pos.cost -= cost_share
        else:
            pos.realized_pnl += proceeds
        pos.quantity = max(pos.quantity - trade.quantity, 0)
        if pos.quantity == 0 and pos.cost != 0:
            # Rounding remainder on full close cannot be carried as an asset;
            # book it into realized P&L instead of silently dropping it.
            pos.realized_pnl -= pos.cost
            pos.cost = Decimal()
    return positions


class PositionBookService:
    def __init__(
        self,
        *,
        book_repository: PositionBookRepository | None = None,
        security_repository: SecurityRepository | None = None,
        daily_bar_repository: DailyBarRepository | None = None,
    ) -> None:
        self.book_repository = book_repository or PositionBookRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()

    # ------------------------------------------------------------------
    # Book CRUD
    # ------------------------------------------------------------------
    def list_books(self, db: Session) -> list[PositionBookRead]:
        return [self._book_to_read(book) for book in self.book_repository.list_books(db)]

    def create_book(self, db: Session, payload: PositionBookCreateRequest) -> PositionBookRead:
        name = payload.name.strip()
        if not name:
            raise PositionBookValidationError("position book name must not be blank")
        if self.book_repository.get_book_by_name(db, name=name) is not None:
            raise PositionBookConflict(f"position book name already exists: {name}")
        book = PositionBook(
            name=name,
            cash_quants=to_quants(payload.cash),
            note=payload.note,
        )
        self.book_repository.save_book(db, book=book)
        return self._book_to_read(book)

    def update_book(
        self,
        db: Session,
        *,
        book_id: int,
        payload: PositionBookUpdateRequest,
    ) -> PositionBookRead:
        book = self._require_book(db, book_id)
        if payload.name is not None:
            name = payload.name.strip()
            if not name:
                raise PositionBookValidationError("position book name must not be blank")
            existing = self.book_repository.get_book_by_name(db, name=name)
            if existing is not None and existing.id != book_id:
                raise PositionBookConflict(f"position book name already exists: {name}")
            book.name = name
        if payload.cash is not None:
            book.cash_quants = to_quants(payload.cash)
        if payload.note is not None:
            book.note = payload.note
        self.book_repository.save_book(db, book=book)
        return self._book_to_read(book)

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------
    def list_trades(self, db: Session, *, book_id: int) -> list[PositionTradeRead]:
        self._require_book(db, book_id)
        trades = self.book_repository.list_trades(db, book_id=book_id)
        return [self._trade_to_read(trade) for trade in trades]

    def add_trade(
        self,
        db: Session,
        market_db: Session,
        *,
        book_id: int,
        payload: PositionTradeCreateRequest,
        idempotency_key: str,
    ) -> PositionTradeRead:
        self._require_book(db, book_id)
        existing = self.book_repository.get_trade_by_idempotency_key(
            db, idempotency_key=idempotency_key
        )
        request_values = {
            "book_id": book_id,
            "side": payload.side,
            "symbol": payload.symbol,
            "quantity": payload.quantity,
            "price_quants": to_quants(payload.price),
            "fee_quants": to_quants(payload.fee),
            "trade_date": payload.trade_date,
        }
        if existing is not None:
            if self._trade_matches(existing, request_values):
                return self._trade_to_read(existing)
            raise PositionBookConflict(
                "idempotency key was already used with a different trade request"
            )

        if payload.trade_date > date.today():
            raise PositionBookValidationError("trade_date must not be in the future")
        securities = self.security_repository.get_many(market_db, symbols={payload.symbol})
        if not securities:
            raise PositionBookValidationError(
                "symbol is missing from market master data; import it before recording trades"
            )
        if payload.side == "sell":
            net = self._net_quantity(db, book_id=book_id, symbol=payload.symbol)
            if net - payload.quantity < 0:
                raise PositionBookValidationError(
                    "sell quantity exceeds the recorded net position for this symbol"
                )

        trade = PositionTrade(idempotency_key=idempotency_key, **request_values)
        try:
            self.book_repository.save_trade(db, trade=trade)
        except IntegrityError:
            # Concurrent insert raced past the pre-check above (single-user
            # ledger, but retries can overlap). Resolve it as replay/conflict
            # instead of surfacing an opaque 500.
            db.rollback()
            existing = self.book_repository.get_trade_by_idempotency_key(
                db, idempotency_key=idempotency_key
            )
            if existing is not None and self._trade_matches(existing, request_values):
                return self._trade_to_read(existing)
            raise PositionBookConflict(
                "idempotency key was already used with a different trade request"
            ) from None
        return self._trade_to_read(trade)

    def delete_trade(self, db: Session, *, book_id: int, trade_id: int) -> None:
        self._require_book(db, book_id)
        trade = self.book_repository.get_trade(db, book_id=book_id, trade_id=trade_id)
        if trade is None:
            raise PositionTradeNotFound("position trade not found")
        symbol = trade.symbol
        self.book_repository.delete_trade(db, trade=trade)
        if self._net_quantity(db, book_id=book_id, symbol=symbol) < 0:
            raise PositionBookValidationError(
                "deleting this trade would leave a negative net position for the symbol"
            )

    # ------------------------------------------------------------------
    # Valuation / analysis
    # ------------------------------------------------------------------
    def get_valuation(
        self,
        positions_db: Session,
        market_db: Session,
        *,
        book_id: int,
    ) -> tuple[PositionBook, Decimal, dict[str, SymbolValuation], date | None]:
        """Value the ledger at the latest trusted market prices.

        Returns the book, its cash, per-symbol valuations and the overall
        price as-of date (max bar date across priced symbols).
        """
        book = self._require_book(positions_db, book_id)
        trades = self.book_repository.list_trades(positions_db, book_id=book_id)
        ledger = replay_ledger(trades)
        open_positions = {
            symbol: pos for symbol, pos in ledger.items() if pos.quantity > 0
        }
        # Fully closed symbols with realized P&L stay in the result so the
        # book-level realized figure survives a position closing out entirely.
        relevant = {
            symbol: pos
            for symbol, pos in ledger.items()
            if pos.quantity > 0 or pos.realized_pnl != 0
        }
        bars = {
            bar.symbol: bar
            for bar in self.daily_bar_repository.list_latest_consumable_by_symbols(
                market_db,
                symbols=set(open_positions),
                adjust_type="none",
            )
            if open_positions
        }
        cash = from_quants(book.cash_quants)
        valuations: dict[str, SymbolValuation] = {}
        price_as_of: date | None = None
        for symbol in sorted(relevant):
            pos = relevant[symbol]
            average_cost = (
                pos.cost / Decimal(pos.quantity)
                if pos.quantity > 0
                else Decimal()
            ).quantize(COST_QUANTUM, rounding=ROUND_HALF_UP)
            valuation = SymbolValuation(
                symbol=symbol,
                quantity=pos.quantity,
                cost_basis=quantize_money(pos.cost),
                average_cost=average_cost,
                realized_pnl=quantize_money(pos.realized_pnl),
            )
            # Out-of-order entries (sell recorded before its covering buy) make
            # the replay book proceeds as fully realized; surface the flag so
            # the ledger never silently overstates realized P&L.
            valuation.extras["sequence_anomaly"] = pos.sequence_anomaly
            bar = bars.get(symbol)
            finalize_valuation(
                valuation,
                price=bar.close if bar else None,
                price_as_of=bar.trade_date if bar else None,
                price_quality_status=bar.quality_status if bar else None,
            )
            if bar is not None and (price_as_of is None or bar.trade_date > price_as_of):
                price_as_of = bar.trade_date
            valuations[symbol] = valuation
        return book, cash, valuations, price_as_of

    def get_holdings(
        self,
        positions_db: Session,
        market_db: Session,
        *,
        book_id: int,
    ) -> PositionBookHoldingsRead:
        book, cash, valuations, price_as_of = self.get_valuation(
            positions_db,
            market_db,
            book_id=book_id,
        )
        securities = {
            security.symbol: security
            for security in self.security_repository.get_many(
                market_db, symbols=set(valuations)
            )
            if valuations
        }
        positions_market_value = sum(
            (valuation.market_value for valuation in valuations.values()), Decimal()
        )
        total_equity = quantize_money(cash + positions_market_value)

        holdings: list[PositionHoldingRead] = []
        for symbol, valuation in valuations.items():
            if valuation.quantity <= 0:
                # Closed positions only contribute to book-level realized P&L.
                continue
            security = securities.get(symbol)
            weight = (
                valuation.market_value / total_equity if total_equity > 0 else Decimal()
            )
            holdings.append(
                PositionHoldingRead(
                    symbol=symbol,
                    name=security.name if security else None,
                    industry=security.industry if security else None,
                    security_type=security.security_type if security else None,
                    exchange=security.exchange if security else None,
                    quantity=valuation.quantity,
                    average_cost=valuation.average_cost,
                    cost_basis=valuation.cost_basis,
                    price=valuation.price,
                    price_as_of=valuation.price_as_of,
                    price_quality_status=valuation.price_quality_status,
                    price_stale=valuation.price_stale,
                    sequence_anomaly=bool(valuation.extras.get("sequence_anomaly", False)),
                    market_value=valuation.market_value,
                    unrealized_pnl=valuation.unrealized_pnl,
                    unrealized_pnl_pct=valuation.unrealized_pnl_pct,
                    realized_pnl=valuation.realized_pnl,
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
                if valuation.quantity > 0
            ],
            total_equity=total_equity,
        )
        total_realized = sum(
            (valuation.realized_pnl for valuation in valuations.values()), Decimal()
        )
        return PositionBookHoldingsRead(
            book_id=book.id,
            book_name=book.name,
            price_as_of=price_as_of,
            price_stale=any(valuation.price_stale for valuation in valuations.values()),
            sequence_anomaly=any(
                valuation.extras.get("sequence_anomaly", False)
                for valuation in valuations.values()
            ),
            cash=quantize_money(cash),
            positions_market_value=quantize_money(positions_market_value),
            total_equity=total_equity,
            realized_pnl=quantize_money(total_realized),
            concentration=PositionConcentrationRead(
                position_count=len(holdings),
                hhi=hhi,
                top5_weight=top5,
                max_symbol_weight=max_weight,
            ),
            industry_exposure=[
                PositionIndustryExposureRead(**row) for row in exposure_rows
            ],
            holdings=holdings,
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _require_book(self, db: Session, book_id: int) -> PositionBook:
        book = self.book_repository.get_book(db, book_id=book_id)
        if book is None:
            raise PositionBookNotFound("position book not found")
        return book

    def _net_quantity(self, db: Session, *, book_id: int, symbol: str) -> int:
        net = 0
        for trade in self.book_repository.list_trades(db, book_id=book_id, symbol=symbol):
            net += trade.quantity if trade.side == "buy" else -trade.quantity
        return net

    @staticmethod
    def _trade_matches(trade: PositionTrade, values: dict) -> bool:
        return all(
            getattr(trade, field_name) == expected
            for field_name, expected in values.items()
        )

    @staticmethod
    def _book_to_read(book: PositionBook) -> PositionBookRead:
        return PositionBookRead(
            id=book.id,
            name=book.name,
            cash=from_quants(book.cash_quants),
            note=book.note,
            created_at=book.created_at,
            updated_at=book.updated_at,
        )

    @staticmethod
    def _trade_to_read(trade: PositionTrade) -> PositionTradeRead:
        return PositionTradeRead(
            id=trade.id,
            symbol=trade.symbol,
            side=trade.side,
            quantity=trade.quantity,
            price=from_quants(trade.price_quants),
            fee=from_quants(trade.fee_quants),
            trade_date=trade.trade_date,
            note=trade.note,
            created_at=trade.created_at,
        )
