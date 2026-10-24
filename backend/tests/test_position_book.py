from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.position_book import (
    PositionBookConflict,
    PositionBookService,
    PositionBookValidationError,
    replay_ledger,
)

NOW = datetime(2026, 7, 21, tzinfo=UTC)


class FakeBookRepository:
    def __init__(self) -> None:
        self.books: dict[int, SimpleNamespace] = {}
        self.trades: list[SimpleNamespace] = []
        self.next_book_id = 1
        self.next_trade_id = 1

    def add_book(self, name: str, cash_quants: int = 0) -> SimpleNamespace:
        book = SimpleNamespace(
            id=self.next_book_id,
            name=name,
            cash_quants=cash_quants,
            note=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.next_book_id += 1
        self.books[book.id] = book
        return book

    def list_books(self, db):
        return list(self.books.values())

    def get_book(self, db, *, book_id: int):
        return self.books.get(book_id)

    def get_book_by_name(self, db, *, name: str):
        return next((book for book in self.books.values() if book.name == name), None)

    def save_book(self, db, *, book):
        # Real SQLite flush assigns the autoincrement id and server-default
        # timestamps; emulate that for the ORM object under test.
        if book.id is None:
            book.id = self.next_book_id
            self.next_book_id += 1
            book.created_at = NOW
            book.updated_at = NOW
        self.books[book.id] = book
        return book

    def add_trade(
        self,
        *,
        book_id: int,
        symbol: str,
        side: str,
        quantity: int,
        price_quants: int,
        fee_quants: int = 0,
        trade_date: date | None = None,
    ) -> SimpleNamespace:
        trade = SimpleNamespace(
            id=self.next_trade_id,
            book_id=book_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price_quants=price_quants,
            fee_quants=fee_quants,
            trade_date=trade_date or date(2026, 7, 20),
            note=None,
            idempotency_key=f"key-{self.next_trade_id}",
            created_at=NOW,
            updated_at=NOW,
        )
        self.next_trade_id += 1
        self.trades.append(trade)
        return trade

    def list_trades(self, db, *, book_id: int, symbol: str | None = None):
        rows = [trade for trade in self.trades if trade.book_id == book_id]
        if symbol is not None:
            rows = [trade for trade in rows if trade.symbol == symbol]
        return sorted(rows, key=lambda trade: (trade.trade_date, trade.id))

    def get_trade(self, db, *, book_id: int, trade_id: int):
        return next(
            (
                trade
                for trade in self.trades
                if trade.book_id == book_id and trade.id == trade_id
            ),
            None,
        )

    def get_trade_by_idempotency_key(self, db, *, idempotency_key: str):
        return next(
            (trade for trade in self.trades if trade.idempotency_key == idempotency_key),
            None,
        )

    def save_trade(self, db, *, trade):
        if trade.id is None:
            trade.id = self.next_trade_id
            self.next_trade_id += 1
            trade.created_at = NOW
            trade.updated_at = NOW
        self.trades.append(trade)
        return trade

    def delete_trade(self, db, *, trade):
        self.trades = [item for item in self.trades if item.id != trade.id]


class FakeSecurityRepository:
    def __init__(self, symbols) -> None:
        self.symbols = set(symbols)

    def get_many(self, db, *, symbols: set[str]):
        return [
            SimpleNamespace(
                symbol=symbol,
                name=f"证券{symbol}",
                industry="银行" if symbol.startswith("6") else "白酒",
                security_type="stock",
                exchange="SH",
            )
            for symbol in sorted(set(symbols) & self.symbols)
        ]


class FakeBarRepository:
    def __init__(self, prices: dict[str, str]) -> None:
        self.prices = {symbol: Decimal(price) for symbol, price in prices.items()}

    def list_latest_consumable_by_symbols(self, db, *, symbols: set[str], adjust_type: str):
        return [
            SimpleNamespace(
                symbol=symbol,
                trade_date=date(2026, 7, 21),
                adjust_type=adjust_type,
                close=self.prices[symbol],
                quality_status="trusted",
            )
            for symbol in sorted(set(symbols))
            if symbol in self.prices
        ]


def _service(book_repository=None, prices: dict[str, str] | None = None):
    return PositionBookService(
        book_repository=book_repository or FakeBookRepository(),
        security_repository=FakeSecurityRepository({"600000.SH", "600519.SH"}),
        daily_bar_repository=FakeBarRepository(
            prices if prices is not None else {"600000.SH": "12.5", "600519.SH": "100"}
        ),
    )


def test_create_book_round_trips_cash_and_rejects_duplicate_name() -> None:
    repository = FakeBookRepository()
    service = _service(book_repository=repository)
    created = service.create_book(
        None,
        SimpleNamespace(name="主账户", cash=Decimal("10000.5"), note=None),
    )
    assert created.cash == Decimal("10000.5000")

    with pytest.raises(PositionBookConflict, match="already exists"):
        service.create_book(None, SimpleNamespace(name="主账户", cash=Decimal("0"), note=None))


def test_buy_and_sell_replay_derives_cost_realized_and_unrealized() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="buy",
        quantity=100,
        price_quants=100000,
        fee_quants=50000,
    )
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="buy",
        quantity=100,
        price_quants=110000,
        fee_quants=50000,
    )
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="sell",
        quantity=100,
        price_quants=120000,
        fee_quants=50000,
    )
    service = _service(book_repository=repository)

    report = service.get_holdings(None, None, book_id=1)

    assert len(report.holdings) == 1
    holding = report.holdings[0]
    assert holding.quantity == 100
    assert holding.average_cost == Decimal("10.550000")
    assert holding.realized_pnl == Decimal("140.0000")
    assert holding.cost_basis == Decimal("1055.0000")
    assert holding.price == Decimal("12.5")
    assert holding.market_value == Decimal("1250.0000")
    assert holding.unrealized_pnl == Decimal("195.0000")
    assert holding.weight == Decimal("1.0000000000")
    assert report.total_equity == Decimal("1250.0000")
    assert report.realized_pnl == Decimal("140.0000")
    assert report.price_as_of == date(2026, 7, 21)
    assert report.price_stale is False
    assert report.industry_exposure[0].industry == "银行"


def test_missing_trusted_price_falls_back_to_cost_valuation() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1, symbol="600000.SH", side="buy", quantity=100, price_quants=100000
    )
    service = _service(book_repository=repository, prices={})

    report = service.get_holdings(None, None, book_id=1)

    holding = report.holdings[0]
    assert holding.price is None
    assert holding.price_stale is True
    assert holding.market_value == holding.cost_basis == Decimal("1000.0000")
    assert holding.unrealized_pnl is None
    assert report.price_stale is True
    assert report.price_as_of is None


def test_sell_beyond_net_position_is_rejected() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1, symbol="600000.SH", side="buy", quantity=100, price_quants=100000
    )
    service = _service(book_repository=repository)

    with pytest.raises(PositionBookValidationError, match="exceeds"):
        service.add_trade(
            None,
            None,
            book_id=1,
            payload=SimpleNamespace(
                side="sell",
                symbol="600000.SH",
                quantity=200,
                price=Decimal("10"),
                fee=Decimal("0"),
                trade_date=date(2026, 7, 21),
                note=None,
            ),
            idempotency_key="sell-1",
        )


def test_delete_buy_that_breaks_net_position_is_rejected() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1, symbol="600000.SH", side="buy", quantity=100, price_quants=100000
    )
    repository.add_trade(
        book_id=1, symbol="600000.SH", side="sell", quantity=100, price_quants=110000
    )
    service = _service(book_repository=repository)

    with pytest.raises(PositionBookValidationError, match="negative net position"):
        service.delete_trade(None, book_id=1, trade_id=1)


def test_trade_idempotency_replays_same_request_and_conflicts_on_difference() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    service = _service(book_repository=repository)
    payload = SimpleNamespace(
        side="buy",
        symbol="600000.SH",
        quantity=100,
        price=Decimal("10"),
        fee=Decimal("0"),
        trade_date=date(2026, 7, 20),
        note=None,
    )
    first = service.add_trade(None, None, book_id=1, payload=payload, idempotency_key="trade-1")
    replay = service.add_trade(None, None, book_id=1, payload=payload, idempotency_key="trade-1")
    assert first.id == replay.id

    with pytest.raises(PositionBookConflict, match="different trade request"):
        service.add_trade(
            None,
            None,
            book_id=1,
            payload=SimpleNamespace(**{**payload.__dict__, "quantity": 200}),
            idempotency_key="trade-1",
        )


def test_replay_ledger_flags_out_of_order_oversell() -> None:
    repository = FakeBookRepository()
    later_buy = repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="buy",
        quantity=100,
        price_quants=100000,
        trade_date=date(2026, 7, 22),
    )
    earlier_sell = repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="sell",
        quantity=100,
        price_quants=110000,
        trade_date=date(2026, 7, 20),
    )
    ledger = replay_ledger([later_buy, earlier_sell])

    position = ledger["600000.SH"]
    assert position.sequence_anomaly is True
    # The uncovered sell books its proceeds as realized; the later buy then
    # re-opens a 100-share position at its own cost.
    assert position.quantity == 100
    assert position.realized_pnl == Decimal("1100.0000")
    assert position.cost == Decimal("1000.0000")


def test_holdings_expose_sequence_anomaly_for_out_of_order_ledger() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="buy",
        quantity=100,
        price_quants=100000,
        trade_date=date(2026, 7, 22),
    )
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="sell",
        quantity=100,
        price_quants=110000,
        trade_date=date(2026, 7, 20),
    )
    service = _service(book_repository=repository)

    report = service.get_holdings(None, None, book_id=1)

    assert report.sequence_anomaly is True
    assert report.holdings[0].sequence_anomaly is True


def test_holdings_sequence_anomaly_false_for_well_ordered_ledger() -> None:
    repository = FakeBookRepository()
    repository.add_book("主账户")
    repository.add_trade(
        book_id=1,
        symbol="600000.SH",
        side="buy",
        quantity=100,
        price_quants=100000,
        trade_date=date(2026, 7, 20),
    )
    service = _service(book_repository=repository)

    report = service.get_holdings(None, None, book_id=1)

    assert report.sequence_anomaly is False
    assert report.holdings[0].sequence_anomaly is False
