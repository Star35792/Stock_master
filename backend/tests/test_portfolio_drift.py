from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.portfolio_drift import (
    PortfolioDriftService,
    PortfolioDriftValidationError,
)
from app.services.portfolio_readiness import PaperAccountNotFound, PortfolioPlanNotFound
from app.services.position_metrics import SymbolValuation

NOW = datetime(2026, 7, 21, tzinfo=UTC)


class FakePlanRepository:
    def __init__(self, plan=None, targets=None) -> None:
        self.plan = plan
        self.targets = targets or []

    def get_with_targets(self, db, *, plan_id: int):
        if self.plan is None or self.plan.id != plan_id:
            return None, []
        return self.plan, self.targets


class FakePaperRepository:
    def __init__(self, cash="1000", positions=None) -> None:
        self.account = SimpleNamespace(id=7, name="main", cash_balance=Decimal(cash))
        self.positions = positions or []

    def get_account(self, db, *, account_id: int):
        return self.account if account_id == 7 else None

    def list_positions(self, db, *, account_id: int):
        return self.positions


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


def _plan(plan_id=3, total="2500", target_cash="250"):
    return SimpleNamespace(
        id=plan_id,
        name="draft",
        total_equity=Decimal(total),
        target_cash=Decimal(target_cash),
        created_at=NOW,
    )


def _target(symbol="600000.SH", weight="0.4"):
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        industry="银行",
        final_weight=Decimal(weight),
    )


def _valuation(symbol="600000.SH", quantity=150, price="10"):
    return SymbolValuation(
        symbol=symbol,
        quantity=quantity,
        cost_basis=Decimal("1000"),
        average_cost=Decimal("10"),
        price=Decimal(price),
        price_as_of=date(2026, 7, 21),
        market_value=Decimal(quantity) * Decimal(price),
    )


class StubBookValuation:
    def __init__(self, cash: str, valuations, price_as_of=date(2026, 7, 21)) -> None:
        self.book = SimpleNamespace(id=5, name="主账户")
        self.cash = Decimal(cash)
        self.valuations = valuations
        self.price_as_of = price_as_of

    def get_valuation(self, positions_db, market_db, *, book_id):
        return self.book, self.cash, self.valuations, self.price_as_of


def _drift_service(
    plan=None,
    targets=None,
    book_valuation: StubBookValuation | None = None,
    paper_repository=None,
    prices: dict[str, str] | None = None,
):
    return PortfolioDriftService(
        plan_repository=FakePlanRepository(plan, targets),
        book_repository=SimpleNamespace(get_book=lambda db, *, book_id: None)
        if book_valuation is None
        else SimpleNamespace(
            get_book=lambda db, *, book_id: SimpleNamespace(id=book_id, name="主账户")
        ),
        paper_repository=paper_repository
        or FakePaperRepository(
            cash="1000",
            positions=[
                SimpleNamespace(symbol="600000.SH", quantity=150, average_cost=Decimal("10"))
            ],
        ),
        daily_bar_repository=FakeBarRepository(
            prices if prices is not None else {"600000.SH": "10"}
        ),
        position_book_service=book_valuation,
    )


def test_drift_requires_exactly_one_source() -> None:
    service = _drift_service()
    with pytest.raises(PortfolioDriftValidationError, match="exactly one"):
        service.get_drift(None, None, plan_id=3)
    with pytest.raises(PortfolioDriftValidationError, match="exactly one"):
        service.get_drift(None, None, plan_id=3, position_book_id=5, paper_account_id=7)


def test_drift_rejects_bad_threshold() -> None:
    service = _drift_service(
        plan=_plan(), targets=[_target()], book_valuation=StubBookValuation("1000", {})
    )
    with pytest.raises(PortfolioDriftValidationError, match="threshold"):
        service.get_drift(None, None, plan_id=3, position_book_id=5, threshold=Decimal("0"))


def test_drift_missing_plan_raises_not_found() -> None:
    service = _drift_service(book_valuation=StubBookValuation("1000", {}))
    with pytest.raises(PortfolioPlanNotFound):
        service.get_drift(None, None, plan_id=99, position_book_id=5)


def test_book_drift_flags_overweight_and_sizes_target_lots() -> None:
    service = _drift_service(
        plan=_plan(),
        targets=[_target()],
        book_valuation=StubBookValuation("1000", {"600000.SH": _valuation()}),
    )
    report = service.get_drift(None, None, plan_id=3, position_book_id=5)

    assert report.source == "position_book"
    assert report.source_name == "主账户"
    assert report.total_equity == Decimal("2500.0000")
    row = report.rows[0]
    assert row.current_weight == Decimal("0.6000000000")
    assert row.target_weight == Decimal("0.4000000000")
    assert row.drift == Decimal("0.2000000000")
    assert row.direction == "sell"
    assert row.current_quantity == 150
    assert row.target_quantity == 100
    assert report.summary.off_threshold_count == 1
    assert report.summary.max_abs_drift == Decimal("0.2000000000")
    assert report.summary.cash_weight == Decimal("0.4000000000")
    assert report.summary.cash_target_weight == Decimal("0.1000000000")
    assert report.price_as_of == date(2026, 7, 21)


def test_paper_drift_flags_underweight_and_handles_missing_price() -> None:
    service = _drift_service(
        plan=_plan(),
        targets=[_target(symbol="600000.SH", weight="0.9")],
        prices={},
    )
    report = service.get_drift(None, None, plan_id=3, paper_account_id=7)

    assert report.source == "paper_account"
    assert report.source_id == 7
    row = report.rows[0]
    # No trusted bar: valuation falls back to cost basis 1500, cash 1000 -> 2500.
    assert row.current_weight == Decimal("0.6000000000")
    assert row.drift == Decimal("-0.3000000000")
    assert row.direction == "buy"
    assert row.target_quantity is None
    assert "no trusted price for lot sizing" in row.reasons


def test_paper_drift_unknown_account_raises() -> None:
    service = _drift_service(plan=_plan(), targets=[_target()])
    with pytest.raises(PaperAccountNotFound):
        service.get_drift(None, None, plan_id=3, paper_account_id=99)


def test_hold_within_threshold_has_no_target_quantity_change() -> None:
    service = _drift_service(
        plan=_plan(),
        targets=[_target(weight="0.6")],
        book_valuation=StubBookValuation("1000", {"600000.SH": _valuation()}),
    )
    report = service.get_drift(None, None, plan_id=3, position_book_id=5, threshold=Decimal("0.05"))

    row = report.rows[0]
    assert row.direction == "hold"
    # Lot sizing floors to whole 100-share lots: 0.6 * 2500 / 10 = 150 -> 100.
    assert row.target_quantity == 100
    assert report.summary.off_threshold_count == 0
