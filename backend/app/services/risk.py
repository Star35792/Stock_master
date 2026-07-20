from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.portfolio import PortfolioPlanRepository
from app.repositories.risk import RiskCheckRepository
from app.risk import OrderRiskDecision, OrderRiskInput, PreTradeRiskEngine, RiskLimits


@dataclass(frozen=True)
class PersistedRiskCheck:
    check_id: int
    passed: bool
    decisions: tuple[OrderRiskDecision, ...]


class PortfolioRiskService:
    def __init__(
        self,
        plan_repository: PortfolioPlanRepository | None = None,
        risk_repository: RiskCheckRepository | None = None,
        engine: PreTradeRiskEngine | None = None,
    ) -> None:
        self.plan_repository = plan_repository or PortfolioPlanRepository()
        self.risk_repository = risk_repository or RiskCheckRepository()
        self.engine = engine or PreTradeRiskEngine()

    def check_plan(
        self,
        db: Session,
        *,
        plan_id: int,
        current_drawdown: Decimal,
        average_daily_amounts: dict[str, Decimal],
        limits: RiskLimits,
        code_version: str,
    ) -> PersistedRiskCheck:
        plan, targets = self.plan_repository.get_with_targets(db, plan_id=plan_id)
        if plan is None:
            raise ValueError("portfolio plan not found")

        current_industry_weights: dict[str, Decimal] = {}
        projected_industry_weights: dict[str, Decimal] = {}
        for target in targets:
            current_weight = (
                Decimal(target.current_quantity) * target.price / plan.total_equity
            )
            current_industry_weights[target.industry] = (
                current_industry_weights.get(target.industry, Decimal()) + current_weight
            )
            projected_industry_weights[target.industry] = (
                projected_industry_weights.get(target.industry, Decimal())
                + target.final_weight
            )

        decisions = []
        for target in targets:
            if target.order_quantity == 0:
                continue
            current_symbol_weight = (
                Decimal(target.current_quantity) * target.price / plan.total_equity
            )
            decisions.append(
                self.engine.check(
                    order=OrderRiskInput(
                        symbol=target.symbol,
                        industry=target.industry,
                        order_quantity=target.order_quantity,
                        price=target.price,
                        current_symbol_weight=current_symbol_weight,
                        projected_symbol_weight=target.final_weight,
                        current_industry_weight=current_industry_weights[target.industry],
                        projected_industry_weight=projected_industry_weights[target.industry],
                        average_daily_amount=average_daily_amounts.get(target.symbol),
                        current_drawdown=current_drawdown,
                    ),
                    limits=limits,
                )
            )
        check = self.risk_repository.save(
            db,
            plan_id=plan_id,
            current_drawdown=current_drawdown,
            limits=limits,
            decisions=decisions,
            code_version=code_version,
        )
        return PersistedRiskCheck(
            check_id=check.id,
            passed=check.passed,
            decisions=tuple(decisions),
        )
