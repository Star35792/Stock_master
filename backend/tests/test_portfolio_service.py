from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.portfolio import (
    ConstraintEnforcementMode,
    PortfolioAsset,
    PortfolioConstraints,
    PortfolioRiskBudget,
)
from app.services.portfolio import (
    PortfolioConstructionService,
    PortfolioIdempotencyConflict,
)


class FakeSession:
    def flush(self):
        return None

    def rollback(self):
        return None


class FakePortfolioRepository:
    def __init__(self) -> None:
        self.records: dict[int, SimpleNamespace] = {}
        self.targets: dict[int, list[object]] = {}
        self.by_key: dict[str, SimpleNamespace] = {}
        self.save_count = 0

    def get_by_idempotency_key(self, db, *, idempotency_key: str):
        return self.by_key.get(idempotency_key)

    def get_with_targets(self, db, *, plan_id: int):
        return self.records.get(plan_id), self.targets.get(plan_id, [])

    def save(self, db, **kwargs):
        self.save_count += 1
        plan_id = self.save_count
        record = SimpleNamespace(
            id=plan_id,
            name=kwargs["name"],
            as_of_date=kwargs["as_of_date"],
            parameters=kwargs["parameters"],
            total_equity=kwargs["plan"].total_equity,
            cash_before=kwargs["plan"].cash_before,
            target_cash=kwargs["plan"].target_cash,
            cash_after=kwargs["plan"].cash_after,
            code_version=kwargs["code_version"],
            idempotency_key=kwargs["idempotency_key"],
            request_hash=kwargs["request_hash"],
            input_snapshot=kwargs["input_snapshot"],
            created_at=datetime(2026, 7, 21, tzinfo=UTC),
        )
        self.records[plan_id] = record
        self.targets[plan_id] = list(kwargs["plan"].targets)
        self.by_key[kwargs["idempotency_key"]] = record
        return record


def _snapshot(weight: str = "0.30") -> dict:
    return {
        "name": "draft",
        "as_of_date": "2026-07-21",
        "cash": "100000",
        "assets": [
            {
                "symbol": "600519.SH",
                "security_type": "stock",
                "industry": "食品饮料",
                "price": "200",
                "current_quantity": 0,
                "requested_weight": weight,
                "lot_size": 100,
            }
        ],
        "constraints": {
            "cash_reserve_rate": "0.05",
            "max_symbol_weight": "0.20",
            "max_industry_weight": "0.40",
            "security_type_budgets": {},
        },
        "input_source": "operator_provided",
    }


def _build(service: PortfolioConstructionService, snapshot: dict):
    return service.build(
        FakeSession(),
        name="draft",
        as_of_date=date(2026, 7, 21),
        assets=[
            PortfolioAsset(
                symbol="600519.SH",
                security_type="stock",
                industry="食品饮料",
                price=Decimal("200"),
                current_quantity=0,
                requested_weight=Decimal(snapshot["assets"][0]["requested_weight"]),
                lot_size=100,
            )
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(),
        code_version="test",
        idempotency_key="draft-key",
        input_snapshot=snapshot,
    )


def test_portfolio_service_replays_same_idempotent_draft() -> None:
    repository = FakePortfolioRepository()
    service = PortfolioConstructionService(repository=repository)

    first = _build(service, _snapshot())
    replay = _build(service, _snapshot())

    assert first.plan_id == replay.plan_id == 1
    assert repository.save_count == 1
    assert replay.execution_allowed is False
    assert replay.targets[0].lot_size == 100


def test_portfolio_service_rejects_reused_key_for_different_request() -> None:
    service = PortfolioConstructionService(repository=FakePortfolioRepository())
    _build(service, _snapshot())

    with pytest.raises(PortfolioIdempotencyConflict, match="different request"):
        _build(service, _snapshot("0.10"))


def test_score_ranked_trim_is_recorded_in_the_persisted_parameters() -> None:
    repository = FakePortfolioRepository()
    service = PortfolioConstructionService(repository=repository)
    fake_db = FakeSession()

    service.build(
        fake_db,
        name="trim draft",
        as_of_date=date(2026, 7, 21),
        assets=[
            PortfolioAsset(
                "600519.SH",
                "stock",
                "食品饮料",
                Decimal("200"),
                0,
                Decimal("0.30"),
                score=Decimal("0.9"),
            ),
            PortfolioAsset(
                "000858.SZ",
                "stock",
                "食品饮料",
                Decimal("150"),
                0,
                Decimal("0.30"),
                score=Decimal("0.1"),
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            cash_reserve_rate=Decimal("0"),
            max_symbol_weight=Decimal("1"),
            max_industry_weight=Decimal("0.40"),
            enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
            extra_symbol_caps={"000858.SZ": Decimal("0.05")},
        ),
        code_version="test",
        idempotency_key="trim-1",
        input_snapshot={"assets": []},
    )

    parameters = next(iter(repository.records.values())).parameters
    assert parameters["enforcement_mode"] == "score_ranked_trim"
    assert parameters["extra_symbol_caps"] == {"000858.SZ": "0.05"}


def test_trim_mode_rejects_assets_without_scores() -> None:
    service = PortfolioConstructionService(repository=FakePortfolioRepository())

    with pytest.raises(ValueError, match="requires a score"):
        service.build(
            FakeSession(),
            name="invalid",
            as_of_date=date(2026, 7, 21),
            assets=[
                PortfolioAsset("600519.SH", "stock", "食品饮料", Decimal("200"), 0, Decimal("0.3")),
            ],
            cash=Decimal("100000"),
            constraints=PortfolioConstraints(
                enforcement_mode=ConstraintEnforcementMode.SCORE_RANKED_TRIM,
            ),
            code_version="test",
            idempotency_key="invalid-1",
            input_snapshot={"assets": []},
        )


def test_score_roundtrips_through_persisted_target() -> None:
    repository = FakePortfolioRepository()
    service = PortfolioConstructionService(repository=repository)

    result = service.build(
        FakeSession(),
        name="scored draft",
        as_of_date=date(2026, 7, 21),
        assets=[
            PortfolioAsset(
                "600519.SH",
                "stock",
                "食品饮料",
                Decimal("200"),
                0,
                Decimal("0.20"),
                score=Decimal("0.85"),
            ),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(),
        code_version="test",
        idempotency_key="score-1",
        input_snapshot={"assets": []},
    )

    assert result.targets[0].score == Decimal("0.85")
    saved_target = repository.targets[result.plan_id][0]
    assert saved_target.score == Decimal("0.85")


def test_risk_budget_roundtrips_through_persisted_parameters() -> None:
    repository = FakePortfolioRepository()
    service = PortfolioConstructionService(repository=repository)

    result = service.build(
        FakeSession(),
        name="risk-budget draft",
        as_of_date=date(2026, 7, 21),
        assets=[
            PortfolioAsset("600519.SH", "stock", "食品饮料", Decimal("200"), 0, Decimal("0.20")),
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(
            risk_budget=PortfolioRiskBudget(
                max_industry_weight=Decimal("0.25"),
            ),
        ),
        code_version="test",
        idempotency_key="risk-budget-1",
        input_snapshot={"assets": []},
    )

    parameters = repository.records[result.plan_id].parameters
    assert parameters["risk_budget"]["max_industry_weight"] == "0.25"
    assert result.risk_budget is not None
    assert result.risk_budget.max_industry_weight == Decimal("0.25")


def test_enforcement_mode_roundtrips_through_persisted_parameters() -> None:
    from app.schemas.portfolio import PortfolioConstraintsRequest

    constraints = PortfolioConstraintsRequest(
        enforcement_mode="score_ranked_trim",
        extra_symbol_caps={"600519.SH": Decimal("0.10")},
    )
    persisted = {
        "cash_reserve_rate": "0.05",
        "max_symbol_weight": "0.20",
        "max_industry_weight": "0.40",
        "security_type_budgets": {},
        "enforcement_mode": "score_ranked_trim",
        "extra_symbol_caps": {"600519.SH": "0.10"},
    }
    restored = PortfolioConstraintsRequest.model_validate(persisted)
    assert restored.enforcement_mode is constraints.enforcement_mode
    assert restored.enforcement_mode.value == "score_ranked_trim"
    assert restored.extra_symbol_caps == {"600519.SH": Decimal("0.10")}
