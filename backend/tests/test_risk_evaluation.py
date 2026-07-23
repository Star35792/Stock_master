from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints
from app.schemas.portfolio import PortfolioRiskReadinessRead
from app.services.liquidity_risk import TrustedAdvSnapshot
from app.services.risk_evaluation import (
    BoundRiskEvaluationService,
    RiskEvaluationConflict,
)
from app.services.snapshot_hashes import (
    hash_equity_market_snapshot,
    hash_paper_account_snapshot,
    hash_payload,
)

NOW = datetime(2026, 7, 21, tzinfo=UTC)
VALUATION_DATE = date(2026, 7, 21)
VALUATION_POLICY = {
    "name": "paper_account_daily_close",
    "version": "v1",
    "adjust_type": "none",
    "accepted_quality": "trusted,manual_fixed",
    "price_field": "close",
    "history_scope": "since_monitoring_start",
}


class FakeSession:
    def flush(self):
        return None

    def rollback(self):
        return None


def _plan():
    built = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                symbol="600000.SH",
                security_type="stock",
                industry="银行",
                price=Decimal("100"),
                current_quantity=0,
                requested_weight=Decimal("0.10"),
                lot_size=100,
            )
        ],
        cash=Decimal("100000"),
        constraints=PortfolioConstraints(),
    )
    return (
        SimpleNamespace(
            id=11,
            as_of_date=VALUATION_DATE,
            total_equity=built.total_equity,
            request_hash="p" * 64,
        ),
        list(built.targets),
    )


class FakePlanRepository:
    def get_with_targets(self, db, *, plan_id: int):
        return _plan() if plan_id == 11 else (None, [])


class FakePaperRepository:
    def __init__(self) -> None:
        self.account = SimpleNamespace(
            id=1,
            cash_balance=Decimal("100000.0000"),
            updated_at=NOW,
        )

    def get_account_for_update(self, db, *, account_id: int):
        return self.account if account_id == 1 else None


def _equity(account_hash: str, *, history_status="continuous", observations=20):
    values = {
        "id": 21,
        "account_id": 1,
        "valuation_date": VALUATION_DATE,
        "cash_balance": Decimal("100000.0000"),
        "positions_market_value": Decimal("0.0000"),
        "total_equity": Decimal("100000.0000"),
        "high_watermark": Decimal("100000.0000"),
        "drawdown": Decimal("0E-10"),
        "previous_snapshot_id": 20,
        "history_status": history_status,
        "observation_count": observations,
        "account_snapshot_hash": account_hash,
        "market_snapshot_hash": hash_equity_market_snapshot(VALUATION_DATE, []),
        "valuation_policy": VALUATION_POLICY,
        "code_version": settings.app_code_version,
    }
    values["valuation_hash"] = hash_payload(
        {
            "account_snapshot_hash": values["account_snapshot_hash"],
            "market_snapshot_hash": values["market_snapshot_hash"],
            "cash_balance": str(values["cash_balance"]),
            "positions_market_value": str(values["positions_market_value"]),
            "total_equity": str(values["total_equity"]),
            "high_watermark": str(values["high_watermark"]),
            "drawdown": str(values["drawdown"]),
            "previous_snapshot_id": values["previous_snapshot_id"],
            "history_status": values["history_status"],
            "valuation_policy": values["valuation_policy"],
            "code_version": values["code_version"],
        }
    )
    return SimpleNamespace(**values)


class FakeEquityRepository:
    def __init__(self, equity) -> None:
        self.equity = equity

    def get_with_components(self, db, *, account_id: int, snapshot_id: int):
        if account_id == 1 and snapshot_id == 21:
            return self.equity, []
        return None, []


class FakeReadinessService:
    def __init__(self, account_hash: str) -> None:
        self.account_hash = account_hash

    def check(self, db, *, plan_id: int, paper_account_id: int):
        return PortfolioRiskReadinessRead(
            plan_id=plan_id,
            paper_account_id=paper_account_id,
            ready_for_risk_check=True,
            checked_at=NOW,
            account_snapshot_hash=self.account_hash,
            market_snapshot_hash="m" * 64,
            checks=[],
            blockers=[],
        )


class FakeAdvService:
    def calculate(self, db, *, symbols: set[str], as_of_date: date):
        return {
            symbol: TrustedAdvSnapshot(
                symbol=symbol,
                available=True,
                average_daily_amount=Decimal("1000000"),
                observation_count=20,
                window=20,
                minimum_observations=15,
                start_date=date(2026, 6, 24),
                end_date=as_of_date,
                input_hash="a" * 64,
                daily_bar_ids=tuple(range(1, 21)),
                observations=tuple(
                    {
                        "daily_bar_id": index,
                        "trade_date": as_of_date.isoformat(),
                        "amount": "1000000",
                        "source_name": "hithink",
                        "quality_status": "trusted",
                        "updated_at": NOW.isoformat(),
                    }
                    for index in range(1, 21)
                ),
            )
            for symbol in symbols
        }


class FakeRiskRepository:
    def __init__(self) -> None:
        self.by_key = {}
        self.by_hash = {}
        self.orders = {}
        self.save_count = 0

    def get_by_idempotency_key(self, db, *, idempotency_key: str):
        return self.by_key.get(idempotency_key)

    def get_by_evaluation_hash(self, db, *, evaluation_hash: str):
        return self.by_hash.get(evaluation_hash)

    def save_evaluation(self, db, *, check_values: dict, order_values: list[dict]):
        self.save_count += 1
        check = SimpleNamespace(id=31, created_at=NOW, **check_values)
        orders = [SimpleNamespace(**values) for values in order_values]
        self.by_key[check.idempotency_key] = check
        self.by_hash[check.evaluation_hash] = check
        self.orders[check.id] = orders
        return check

    def get_with_orders(self, db, *, check_id: int):
        check = next(
            (item for item in self.by_key.values() if item.id == check_id),
            None,
        )
        return check, self.orders.get(check_id, [])


def _service(*, history_status="continuous", observations=20, readiness_hash=None):
    paper = FakePaperRepository()
    account_hash = hash_paper_account_snapshot(paper.account, [])
    risk_repository = FakeRiskRepository()
    service = BoundRiskEvaluationService(
        plan_repository=FakePlanRepository(),
        paper_repository=paper,
        equity_repository=FakeEquityRepository(
            _equity(
                account_hash,
                history_status=history_status,
                observations=observations,
            )
        ),
        risk_repository=risk_repository,
        readiness_service=FakeReadinessService(readiness_hash or account_hash),
        adv_service=FakeAdvService(),
    )
    return service, risk_repository


def test_bound_risk_evaluation_uses_server_snapshots_and_adv() -> None:
    service, repository = _service()

    result = service.evaluate(
        FakeSession(),
        plan_id=11,
        paper_account_id=1,
        equity_snapshot_id=21,
        idempotency_key="risk-31",
    )

    assert result.risk_passed is True
    assert result.risk_evaluated is True
    assert result.execution_allowed is False
    assert result.orders_created is False
    assert result.orders[0].average_daily_amount == Decimal("1000000")
    assert result.orders[0].adv_observation_count == 20
    assert repository.save_count == 1


def test_bound_risk_evaluation_blocks_incomplete_equity_history() -> None:
    service, _ = _service(history_status="bootstrap", observations=1)

    result = service.evaluate(
        FakeSession(),
        plan_id=11,
        paper_account_id=1,
        equity_snapshot_id=21,
        idempotency_key="risk-32",
    )

    assert result.risk_passed is False
    assert result.evaluation_status == "blocked"
    assert "equity_history_incomplete" in result.orders[0].rejection_reasons


def test_bound_risk_evaluation_rejects_account_snapshot_drift() -> None:
    service, repository = _service(readiness_hash="x" * 64)

    with pytest.raises(RiskEvaluationConflict, match="changed"):
        service.evaluate(
            FakeSession(),
            plan_id=11,
            paper_account_id=1,
            equity_snapshot_id=21,
            idempotency_key="risk-33",
        )

    assert repository.save_count == 0


def test_bound_risk_evaluation_replays_idempotent_request() -> None:
    service, repository = _service()
    kwargs = {
        "plan_id": 11,
        "paper_account_id": 1,
        "equity_snapshot_id": 21,
        "idempotency_key": "risk-34",
    }

    first = service.evaluate(FakeSession(), **kwargs)
    replay = service.evaluate(FakeSession(), **kwargs)

    assert first.check_id == replay.check_id
    assert repository.save_count == 1
