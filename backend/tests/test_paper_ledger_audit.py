from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.paper_ledger_audit import PaperLedgerAuditService

NOW = datetime(2026, 7, 21, tzinfo=UTC)


class FakeLedgerAuditRepository:
    def __init__(self, facts) -> None:
        self.facts = facts

    def get_facts(self, db, *, account_id: int):
        if self.facts.account is None or self.facts.account.id != account_id:
            return None
        return self.facts


def _consistent_buy_facts():
    return SimpleNamespace(
        account=SimpleNamespace(
            id=1,
            name="本地模拟账户",
            cash_balance=Decimal("799.9400"),
        ),
        orders=[
            SimpleNamespace(
                id=11,
                account_id=1,
                execution_batch_id=21,
                execution_index=0,
                symbol="600000.SH",
                side="buy",
                order_quantity=2,
                status="filled",
            )
        ],
        fills=[
            SimpleNamespace(
                id=31,
                order_id=11,
                account_id=1,
                symbol="600000.SH",
                side="buy",
                quantity=2,
                price=Decimal("100.000000"),
                commission=Decimal("0.060000"),
            )
        ],
        positions=[
            SimpleNamespace(
                symbol="600000.SH",
                quantity=2,
                average_cost=Decimal("100.030000"),
            )
        ],
        completed_batches=[
            SimpleNamespace(
                id=21,
                cash_before=Decimal("1000.0000"),
                cash_after=Decimal("799.9400"),
                fill_count=1,
                total_commission=Decimal("0.060000"),
                completed_at=NOW,
            )
        ],
    )


def test_audit_rebuilds_consistent_cash_and_position_from_fills() -> None:
    service = PaperLedgerAuditService(
        repository=FakeLedgerAuditRepository(_consistent_buy_facts())
    )

    result = service.audit_account(object(), account_id=1)

    assert result.status == "consistent"
    assert result.actual_cash_balance == Decimal("799.9400")
    assert result.expected_cash_balance == Decimal("799.9400")
    assert result.cash_difference == Decimal("0.0000")
    assert result.positions[0].expected_quantity == 2
    assert result.positions[0].actual_quantity == 2
    assert result.positions[0].expected_average_cost == Decimal("100.030000")
    assert result.positions[0].consistent is True
    assert result.issues == []
    assert result.read_only is True
    assert result.auto_repair_performed is False


def test_audit_rejects_fill_that_does_not_match_its_order() -> None:
    facts = _consistent_buy_facts()
    facts.fills[0].symbol = "000001.SZ"
    facts.positions[0].symbol = "000001.SZ"
    service = PaperLedgerAuditService(repository=FakeLedgerAuditRepository(facts))

    result = service.audit_account(object(), account_id=1)

    assert result.status == "inconsistent"
    assert [issue.code for issue in result.issues] == ["fill_order_mismatch"]
    assert result.issues[0].order_id == 11
    assert result.issues[0].fill_id == 31


def test_audit_reports_cash_and_position_differences_without_repairing() -> None:
    facts = _consistent_buy_facts()
    facts.account.cash_balance = Decimal("800.0000")
    facts.positions[0].quantity = 1
    service = PaperLedgerAuditService(repository=FakeLedgerAuditRepository(facts))

    result = service.audit_account(object(), account_id=1)

    assert result.status == "inconsistent"
    assert result.cash_difference == Decimal("0.0600")
    assert result.positions[0].quantity_difference == -1
    assert {issue.code for issue in result.issues} == {
        "account_cash_mismatch",
        "position_state_mismatch",
    }
    assert facts.account.cash_balance == Decimal("800.0000")
    assert facts.positions[0].quantity == 1
    assert result.auto_repair_performed is False


def test_audit_marks_cash_unverifiable_without_completed_batch() -> None:
    facts = SimpleNamespace(
        account=SimpleNamespace(
            id=1,
            name="空白模拟账户",
            cash_balance=Decimal("1000.0000"),
        ),
        orders=[],
        fills=[],
        positions=[],
        completed_batches=[],
    )
    service = PaperLedgerAuditService(repository=FakeLedgerAuditRepository(facts))

    result = service.audit_account(object(), account_id=1)

    assert result.status == "unverifiable"
    assert result.expected_cash_balance is None
    assert result.cash_difference is None
    assert [issue.code for issue in result.issues] == ["cash_history_unverifiable"]


def test_audit_reports_negative_position_sequence_without_crashing() -> None:
    facts = _consistent_buy_facts()
    facts.orders = [
        SimpleNamespace(
            id=11,
            account_id=1,
            execution_batch_id=None,
            execution_index=None,
            symbol="600000.SH",
            side="sell",
            order_quantity=2,
            status="filled",
        ),
        SimpleNamespace(
            id=12,
            account_id=1,
            execution_batch_id=None,
            execution_index=None,
            symbol="600000.SH",
            side="buy",
            order_quantity=2,
            status="filled",
        ),
    ]
    facts.fills = [
        SimpleNamespace(
            id=31,
            order_id=11,
            account_id=1,
            symbol="600000.SH",
            side="sell",
            quantity=2,
            price=Decimal("100"),
            commission=Decimal("0"),
        ),
        SimpleNamespace(
            id=32,
            order_id=12,
            account_id=1,
            symbol="600000.SH",
            side="buy",
            quantity=2,
            price=Decimal("100"),
            commission=Decimal("0"),
        ),
    ]
    facts.positions = []
    facts.completed_batches = []
    facts.account.cash_balance = Decimal("1000")
    service = PaperLedgerAuditService(repository=FakeLedgerAuditRepository(facts))

    result = service.audit_account(object(), account_id=1)

    assert result.status == "inconsistent"
    assert "negative_rebuilt_position" in {issue.code for issue in result.issues}
    assert result.positions[0].expected_quantity == 0
