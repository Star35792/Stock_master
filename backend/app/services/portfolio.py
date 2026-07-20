from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints, PortfolioPlan
from app.repositories.portfolio import PortfolioPlanRepository


@dataclass(frozen=True)
class PersistedPortfolioPlan:
    plan_id: int
    plan: PortfolioPlan


class PortfolioConstructionService:
    def __init__(
        self,
        builder: PortfolioBuilder | None = None,
        repository: PortfolioPlanRepository | None = None,
    ) -> None:
        self.builder = builder or PortfolioBuilder()
        self.repository = repository or PortfolioPlanRepository()

    def build(
        self,
        db: Session,
        *,
        name: str,
        as_of_date: date,
        assets: list[PortfolioAsset],
        cash: Decimal,
        constraints: PortfolioConstraints,
        code_version: str,
    ) -> PersistedPortfolioPlan:
        plan = self.builder.build(
            assets=assets,
            cash=cash,
            constraints=constraints,
        )
        record = self.repository.save(
            db,
            name=name,
            as_of_date=as_of_date,
            parameters={
                "cash_reserve_rate": str(constraints.cash_reserve_rate),
                "max_symbol_weight": str(constraints.max_symbol_weight),
                "max_industry_weight": str(constraints.max_industry_weight),
                "security_type_budgets": {
                    key: str(value)
                    for key, value in (constraints.security_type_budgets or {}).items()
                },
            },
            plan=plan,
            code_version=code_version,
        )
        return PersistedPortfolioPlan(plan_id=record.id, plan=plan)
