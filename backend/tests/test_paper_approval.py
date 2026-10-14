from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.services.paper_approval import (
    PaperApprovalValidationError,
    PaperExecutionApprovalService,
)

NOW = datetime.now(UTC)


class FakeSession:
    def flush(self):
        return None

    def rollback(self):
        return None


class FakeApprovalRepository:
    def __init__(self) -> None:
        self.by_key = {}
        self.by_check = {}
        self.by_id = {}
        self.save_count = 0

    def get_by_idempotency_key(self, db, *, idempotency_key: str):
        return self.by_key.get(idempotency_key)

    def get_by_risk_check_id(self, db, *, risk_check_id: int):
        return self.by_check.get(risk_check_id)

    def get(self, db, *, approval_id: int):
        return self.by_id.get(approval_id)

    def get_for_update(self, db, *, approval_id: int):
        return self.by_id.get(approval_id)

    def save(self, db, *, values: dict):
        self.save_count += 1
        approval = SimpleNamespace(id=41, created_at=NOW, **values)
        self.by_key[approval.idempotency_key] = approval
        self.by_check[approval.risk_check_id] = approval
        self.by_id[approval.id] = approval
        return approval


class FakeRiskService:
    def validate_current_evaluation(self, db, *, check_id: int):
        check = SimpleNamespace(
            id=check_id,
            account_id=1,
            plan_id=11,
            equity_snapshot_id=21,
            evaluation_hash="e" * 64,
            plan_request_hash="p" * 64,
            readiness_account_hash="a" * 64,
            readiness_market_hash="m" * 64,
            readiness_hash="r" * 64,
            adv_snapshot_hash="d" * 64,
            risk_policy_hash="q" * 64,
            code_version=settings.app_code_version,
        )
        order = SimpleNamespace(
            symbol="600000.SH",
            side="buy",
            order_quantity=100,
            reference_price=Decimal("100"),
            current_symbol_weight=Decimal("0"),
            projected_symbol_weight=Decimal("0.1"),
            current_industry_weight=Decimal("0"),
            projected_industry_weight=Decimal("0.1"),
            average_daily_amount=Decimal("1000000"),
            adv_window=20,
            adv_observation_count=20,
            rule_results=[
                {
                    "rule_name": "max_symbol_weight",
                    "passed": True,
                    "reason_code": "passed",
                    "message": "within limit",
                }
            ],
        )
        plan = SimpleNamespace(id=11, as_of_date=date(2026, 7, 21))
        target = SimpleNamespace(
            symbol="600000.SH",
            security_type="stock",
            industry="银行",
            current_quantity=0,
            target_quantity=100,
        )
        equity = SimpleNamespace(
            id=21,
            cash_balance=Decimal("100000"),
            valuation_hash="v" * 64,
        )
        return check, [order], plan, [target], equity


class FakePaperRepository:
    def get_account(self, db, *, account_id: int):
        return SimpleNamespace(id=account_id, name="paper-main")


class FakeDailyBarRepository:
    def __init__(self, bars: dict | None = None) -> None:
        self.bars = bars or {}

    def list_by_symbols_on_date(
        self,
        db,
        *,
        symbols,
        trade_date,
        adjust_type,
    ):
        return [bar for symbol, bar in self.bars.items() if symbol in symbols]


def _service(repository=None, *, daily_bar_repository=None):
    repository = repository or FakeApprovalRepository()
    if daily_bar_repository is None:
        # Default: provide a tradable bar so existing tests do not need
        # to know about the new execution-day tradability check.
        daily_bar_repository = FakeDailyBarRepository(
            {
                "600000.SH": SimpleNamespace(
                    symbol="600000.SH",
                    open=Decimal("100"),
                    close=Decimal("100"),
                    high=Decimal("100"),
                    low=Decimal("100"),
                    pre_close=Decimal("100"),
                    limit_up=Decimal("110"),
                    limit_down=Decimal("90"),
                    is_suspended=False,
                    special_treatment_status=None,
                    quality_status="trusted",
                )
            }
        )
    return (
        PaperExecutionApprovalService(
            repository=repository,
            risk_service=FakeRiskService(),
            paper_repository=FakePaperRepository(),
            daily_bar_repository=daily_bar_repository,
        ),
        repository,
    )


def test_create_paper_execution_approval_binds_order_batch_without_execution() -> None:
    service, repository = _service()

    result = service.create(
        FakeSession(),
        risk_check_id=31,
        expected_evaluation_hash="e" * 64,
        confirmation_phrase="CONFIRM PAPER EXECUTION 31",
        idempotency_key="approval-41",
    )

    assert result.effective_status == "pending"
    assert result.execution_allowed is False
    assert result.orders_created is False
    assert result.consumable_later is True
    assert result.order_summary[0]["risk_classification"] == "risk_increasing"
    assert result.expires_at - result.approved_at == timedelta(minutes=5)
    assert repository.save_count == 1


def test_create_paper_execution_approval_requires_dynamic_phrase() -> None:
    service, repository = _service()

    with pytest.raises(PaperApprovalValidationError, match="CONFIRM PAPER EXECUTION 31"):
        service.create(
            FakeSession(),
            risk_check_id=31,
            expected_evaluation_hash="e" * 64,
            confirmation_phrase="CONFIRM",
            idempotency_key="approval-42",
        )

    assert repository.save_count == 0


def test_create_paper_execution_approval_replays_idempotent_request() -> None:
    service, repository = _service()
    kwargs = {
        "risk_check_id": 31,
        "expected_evaluation_hash": "e" * 64,
        "confirmation_phrase": "CONFIRM PAPER EXECUTION 31",
        "idempotency_key": "approval-43",
    }

    first = service.create(FakeSession(), **kwargs)
    replay = service.create(FakeSession(), **kwargs)

    assert first.approval_id == replay.approval_id
    assert first.expires_at == replay.expires_at
    assert repository.save_count == 1


def test_revoke_pending_paper_execution_approval() -> None:
    service, _ = _service()
    created = service.create(
        FakeSession(),
        risk_check_id=31,
        expected_evaluation_hash="e" * 64,
        confirmation_phrase="CONFIRM PAPER EXECUTION 31",
        idempotency_key="approval-44",
    )

    revoked = service.revoke(
        FakeSession(),
        approval_id=created.approval_id,
        reason="operator cancelled",
        idempotency_key="revoke-44",
    )

    assert revoked.effective_status == "revoked"
    assert revoked.consumable_later is False
    assert revoked.revocation_reason == "operator cancelled"


def test_create_paper_execution_approval_blocks_buy_at_limit_up() -> None:
    from app.services.paper_approval import PaperApprovalConflict

    daily_bar_repository = FakeDailyBarRepository(
        {
            "600000.SH": SimpleNamespace(
                symbol="600000.SH",
                open=Decimal("100"),
                close=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                pre_close=Decimal("100"),
                limit_up=Decimal("100"),
                limit_down=Decimal("90"),
                is_suspended=False,
                special_treatment_status=None,
                quality_status="trusted",
            )
        }
    )
    service, repository = _service(daily_bar_repository=daily_bar_repository)

    with pytest.raises(PaperApprovalConflict, match="buy is blocked"):
        service.create(
            FakeSession(),
            risk_check_id=31,
            expected_evaluation_hash="e" * 64,
            confirmation_phrase="CONFIRM PAPER EXECUTION 31",
            idempotency_key="approval-45",
        )

    assert repository.save_count == 0


def test_create_paper_execution_approval_blocks_when_no_execution_day_bar() -> None:
    from app.services.paper_approval import PaperApprovalConflict

    service, repository = _service(daily_bar_repository=FakeDailyBarRepository({}))

    with pytest.raises(PaperApprovalConflict, match="no trusted none daily bar"):
        service.create(
            FakeSession(),
            risk_check_id=31,
            expected_evaluation_hash="e" * 64,
            confirmation_phrase="CONFIRM PAPER EXECUTION 31",
            idempotency_key="approval-46",
        )

    assert repository.save_count == 0
