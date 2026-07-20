from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.paper_trading import PaperTradingService
from app.trade import PaperBrokerAdapter


class FakePaperTradingRepository:
    def __init__(
        self,
        *,
        account: SimpleNamespace,
        positions: dict[str, SimpleNamespace] | None = None,
    ) -> None:
        self.account = account
        self.positions = positions or {}
        self.orders: list[SimpleNamespace] = []
        self.fills: list[object] = []

    def get_account(self, db: object, *, account_id: int) -> SimpleNamespace | None:
        return self.account if self.account.id == account_id else None

    def get_position(
        self,
        db: object,
        *,
        account_id: int,
        symbol: str,
    ) -> SimpleNamespace | None:
        return self.positions.get(symbol)

    def save_rejected_order(
        self,
        db: object,
        *,
        account_id: int,
        plan_id: int,
        check_id: int,
        order: object,
        rejection_reason: str,
    ) -> SimpleNamespace:
        record = SimpleNamespace(status="rejected", rejection_reason=rejection_reason)
        self.orders.append(record)
        return record

    def save_filled_order(
        self,
        db: object,
        *,
        account: SimpleNamespace,
        plan_id: int,
        check_id: int,
        order: object,
        fill: object,
        cash_balance: Decimal,
        position_quantity: int,
        average_cost: Decimal,
    ) -> SimpleNamespace:
        record = SimpleNamespace(status="filled", rejection_reason=None)
        self.orders.append(record)
        self.fills.append(fill)
        account.cash_balance = cash_balance
        self.positions[fill.symbol] = SimpleNamespace(
            quantity=position_quantity,
            average_cost=average_cost,
        )
        return record


class FakeRiskRepository:
    def __init__(self, *, orders: list[SimpleNamespace]) -> None:
        self.orders = orders

    def get_with_orders(
        self,
        db: object,
        *,
        check_id: int,
    ) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
        return SimpleNamespace(id=check_id, plan_id=1), self.orders


class FakePlanRepository:
    def __init__(self, *, targets: list[SimpleNamespace]) -> None:
        self.targets = targets

    def get_with_targets(
        self,
        db: object,
        *,
        plan_id: int,
    ) -> tuple[SimpleNamespace, list[SimpleNamespace]]:
        return SimpleNamespace(id=plan_id, as_of_date=date(2026, 7, 17)), self.targets


def test_paper_trading_executes_passed_buy_and_updates_account() -> None:
    paper_repository = FakePaperTradingRepository(
        account=SimpleNamespace(id=1, cash_balance=Decimal("10000")),
    )
    service = PaperTradingService(
        paper_repository=paper_repository,
        risk_repository=FakeRiskRepository(
            orders=[
                SimpleNamespace(
                    symbol="AAA",
                    side="buy",
                    order_quantity=100,
                    passed=True,
                )
            ]
        ),
        plan_repository=FakePlanRepository(
            targets=[SimpleNamespace(symbol="AAA", price=Decimal("10"))]
        ),
        broker=PaperBrokerAdapter(commission_rate=Decimal(), slippage_bps=Decimal()),
    )

    result = service.execute_check(object(), account_id=1, check_id=1)

    assert result.fill_count == 1
    assert result.rejected_count == 0
    assert result.cash_balance == Decimal("9000")
    assert paper_repository.positions["AAA"].quantity == 100
    assert paper_repository.positions["AAA"].average_cost == Decimal("10")


def test_paper_trading_rejects_failed_risk_order_without_fill() -> None:
    paper_repository = FakePaperTradingRepository(
        account=SimpleNamespace(id=1, cash_balance=Decimal("10000")),
    )
    service = PaperTradingService(
        paper_repository=paper_repository,
        risk_repository=FakeRiskRepository(
            orders=[
                SimpleNamespace(
                    symbol="AAA",
                    side="buy",
                    order_quantity=100,
                    passed=False,
                )
            ]
        ),
        plan_repository=FakePlanRepository(
            targets=[SimpleNamespace(symbol="AAA", price=Decimal("10"))]
        ),
        broker=PaperBrokerAdapter(commission_rate=Decimal(), slippage_bps=Decimal()),
    )

    result = service.execute_check(object(), account_id=1, check_id=1)

    assert result.fill_count == 0
    assert result.rejected_count == 1
    assert result.cash_balance == Decimal("10000")
    assert paper_repository.orders[0].rejection_reason == "risk_rejected"
    assert paper_repository.fills == []


def test_paper_trading_executes_sell_and_keeps_average_cost() -> None:
    paper_repository = FakePaperTradingRepository(
        account=SimpleNamespace(id=1, cash_balance=Decimal("1000")),
        positions={
            "AAA": SimpleNamespace(quantity=100, average_cost=Decimal("10")),
        },
    )
    service = PaperTradingService(
        paper_repository=paper_repository,
        risk_repository=FakeRiskRepository(
            orders=[
                SimpleNamespace(
                    symbol="AAA",
                    side="sell",
                    order_quantity=-40,
                    passed=True,
                )
            ]
        ),
        plan_repository=FakePlanRepository(
            targets=[SimpleNamespace(symbol="AAA", price=Decimal("12"))]
        ),
        broker=PaperBrokerAdapter(commission_rate=Decimal(), slippage_bps=Decimal()),
    )

    result = service.execute_check(object(), account_id=1, check_id=1)

    assert result.fill_count == 1
    assert result.cash_balance == Decimal("1480")
    assert paper_repository.positions["AAA"].quantity == 60
    assert paper_repository.positions["AAA"].average_cost == Decimal("10")
