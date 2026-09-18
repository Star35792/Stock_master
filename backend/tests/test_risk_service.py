from decimal import Decimal
from types import SimpleNamespace

from app.risk import RiskLimits
from app.services.risk import PortfolioRiskService


class FakePlanRepository:
    def get_with_targets(self, db, *, plan_id: int):
        plan = SimpleNamespace(id=plan_id, total_equity=Decimal("10000"))
        targets = [
            SimpleNamespace(
                symbol="AAA",
                industry="Tech",
                order_quantity=60,
                price=Decimal("10"),
                current_quantity=0,
                final_weight=Decimal("0.06"),
            ),
            SimpleNamespace(
                symbol="BBB",
                industry="Finance",
                order_quantity=60,
                price=Decimal("10"),
                current_quantity=0,
                final_weight=Decimal("0.06"),
            ),
        ]
        return plan, targets


class FakeRiskRepository:
    def save(self, db, **values):
        decisions = values["decisions"]
        return SimpleNamespace(id=1, passed=all(item.passed for item in decisions))


def test_portfolio_risk_service_accumulates_cycle_turnover_in_order() -> None:
    service = PortfolioRiskService(
        plan_repository=FakePlanRepository(),
        risk_repository=FakeRiskRepository(),
    )

    result = service.check_plan(
        None,
        plan_id=1,
        current_drawdown=Decimal("-0.01"),
        average_daily_amounts={
            "AAA": Decimal("100000"),
            "BBB": Decimal("100000"),
        },
        limits=RiskLimits(
            max_symbol_weight=Decimal("0.20"),
            max_industry_weight=Decimal("0.40"),
            max_drawdown=Decimal("0.10"),
            max_amount_participation=Decimal("0.10"),
            max_price_deviation=Decimal("0.02"),
            max_cycle_turnover=Decimal("0.10"),
        ),
        code_version="test",
        market_prices={"AAA": Decimal("10"), "BBB": Decimal("10")},
    )

    assert result.decisions[0].passed is True
    assert result.decisions[1].rejection_reasons == ("max_cycle_turnover_exceeded",)
