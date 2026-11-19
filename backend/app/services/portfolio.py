import json
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.transaction import idempotent_write
from app.portfolio import (
    PortfolioAsset,
    PortfolioBuilder,
    PortfolioConstraints,
)
from app.repositories.portfolio import PortfolioPlanRepository
from app.schemas.common import Page
from app.schemas.portfolio import (
    PortfolioConstraintsRequest,
    PortfolioPlanRead,
    PortfolioPlanSummaryRead,
    PortfolioRiskBudgetRequest,
    PortfolioStyleRiskRead,
    PortfolioTargetRead,
)


def _risk_budget_from_parameters(
    parameters: dict[str, Any],
) -> PortfolioRiskBudgetRequest | None:
    raw = parameters.get("risk_budget")
    if raw is None:
        return None
    return PortfolioRiskBudgetRequest(
        max_industry_weight=Decimal(raw["max_industry_weight"]),
        max_style_exposure=(
            Decimal(raw["max_style_exposure"])
            if raw.get("max_style_exposure") is not None
            else None
        ),
        max_tracking_error=(
            Decimal(raw["max_tracking_error"])
            if raw.get("max_tracking_error") is not None
            else None
        ),
        max_portfolio_volatility=(
            Decimal(raw["max_portfolio_volatility"])
            if raw.get("max_portfolio_volatility") is not None
            else None
        ),
        benchmark=raw.get("benchmark"),
    )


def hash_portfolio_snapshot(input_snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(
        input_snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


class PortfolioIdempotencyConflict(ValueError):
    pass


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
        idempotency_key: str,
        input_snapshot: dict[str, Any],
        style_factor_map: dict[str, tuple[str, str]] | None = None,
        benchmark_symbol: str | None = None,
    ) -> PortfolioPlanRead:
        request_hash = hash_portfolio_snapshot(input_snapshot)
        existing = self.repository.get_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            return self._replay_existing(db, existing, request_hash=request_hash)

        plan = self.builder.build(
            assets=assets,
            cash=cash,
            constraints=constraints,
            db=db,
            as_of_date=as_of_date,
            style_factor_map=style_factor_map,
            benchmark_symbol=benchmark_symbol,
        )
        try:
            with idempotent_write(db):
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
                        "enforcement_mode": str(constraints.enforcement_mode),
                        "extra_symbol_caps": {
                            key: str(value)
                            for key, value in constraints.extra_symbol_caps.items()
                        },
                        "style_factor_map": (
                            {key: list(value) for key, value in style_factor_map.items()}
                            if style_factor_map is not None else None
                        ),
                        "benchmark_symbol": benchmark_symbol,
                        "risk_budget": (
                            {
                                "max_industry_weight": str(
                                    constraints.risk_budget.max_industry_weight
                                ),
                                "max_style_exposure": (
                                    str(constraints.risk_budget.max_style_exposure)
                                    if constraints.risk_budget.max_style_exposure is not None
                                    else None
                                ),
                                "max_tracking_error": (
                                    str(constraints.risk_budget.max_tracking_error)
                                    if constraints.risk_budget.max_tracking_error is not None
                                    else None
                                ),
                                "max_portfolio_volatility": (
                                    str(constraints.risk_budget.max_portfolio_volatility)
                                    if constraints.risk_budget.max_portfolio_volatility is not None
                                    else None
                                ),
                                "benchmark": constraints.risk_budget.benchmark,
                            }
                            if constraints.risk_budget is not None
                            else None
                        ),
                    },
                    plan=plan,
                    code_version=code_version,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    input_snapshot=input_snapshot,
                )
        except IntegrityError:
            existing = self.repository.get_by_idempotency_key(
                db,
                idempotency_key=idempotency_key,
            )
            if existing is None:
                raise
            return self._replay_existing(db, existing, request_hash=request_hash)

        return self._to_read(record, plan.targets)

    def get_plan(self, db: Session, *, plan_id: int) -> PortfolioPlanRead | None:
        record, targets = self.repository.get_with_targets(db, plan_id=plan_id)
        if record is None:
            return None
        return self._to_read(record, targets)

    def list_plans(
        self, db: Session, *, limit: int, offset: int
    ) -> Page[PortfolioPlanSummaryRead]:
        records, total = self.repository.list_plans(db, limit=limit, offset=offset)
        return Page(
            items=[PortfolioPlanSummaryRead.model_validate(record) for record in records],
            total=total,
            limit=limit,
            offset=offset,
        )

    def _replay_existing(
        self,
        db: Session,
        record: Any,
        *,
        request_hash: str,
    ) -> PortfolioPlanRead:
        if record.request_hash != request_hash:
            raise PortfolioIdempotencyConflict(
                "idempotency key was already used with a different request"
            )
        _, targets = self.repository.get_with_targets(db, plan_id=record.id)
        return self._to_read(record, targets)

    @staticmethod
    def _to_read(record: Any, targets: Iterable[Any]) -> PortfolioPlanRead:
        input_snapshot = record.input_snapshot or {}
        lot_sizes = {
            asset["symbol"]: asset.get("lot_size", 100)
            for asset in input_snapshot.get("assets", [])
        }
        target_reads = [
            PortfolioTargetRead(
                symbol=target.symbol,
                security_type=target.security_type,
                industry=target.industry,
                requested_weight=target.requested_weight,
                constrained_weight=target.constrained_weight,
                final_weight=target.final_weight,
                current_quantity=target.current_quantity,
                target_quantity=target.target_quantity,
                order_quantity=target.order_quantity,
                price=target.price,
                lot_size=lot_sizes.get(target.symbol, 100),
                score=target.score,
                reasons=target.reasons,
            )
            for target in targets
        ]
        style_risk = None
        if hasattr(record, "style_risk") and record.style_risk is not None:
            style_risk = PortfolioStyleRiskRead.model_validate(
                record.style_risk, from_attributes=True
            )
        return PortfolioPlanRead(
            plan_id=record.id,
            name=record.name,
            as_of_date=record.as_of_date,
            input_source=input_snapshot.get("input_source", "operator_provided"),
            total_equity=record.total_equity,
            cash_before=record.cash_before,
            target_cash=record.target_cash,
            cash_after=record.cash_after,
            constraints=PortfolioConstraintsRequest.model_validate(record.parameters),
            code_version=record.code_version,
            created_at=record.created_at,
            targets=target_reads,
            risk_budget=_risk_budget_from_parameters(record.parameters),
            style_risk=style_risk,
        )
