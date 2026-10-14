from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config.settings import settings
from app.services.paper_approval import (
    CONFIRMATION_POLICY,
    _default_paper_broker,
    build_approval_hash,
    build_paper_order_batch,
)
from app.services.paper_execution import PaperExecutionConflict, PaperExecutionService

NOW = datetime.now(UTC)


class FakeSession:
    def flush(self):
        return None

    def rollback(self):
        return None


class FakeApprovalRepository:
    def __init__(self, approval) -> None:
        self.approval = approval

    def get_for_update(self, db, *, approval_id: int):
        return self.approval if approval_id == self.approval.id else None


class FakeExecutionRepository:
    def __init__(self) -> None:
        self.by_key = {}
        self.by_approval = {}
        self.by_id = {}
        self.save_count = 0
        self.hide_next_idempotency_lookup = False

    def get(self, db, *, execution_id: int):
        return self.by_id.get(execution_id)

    def get_by_idempotency_key(self, db, *, idempotency_key: str):
        if self.hide_next_idempotency_lookup:
            self.hide_next_idempotency_lookup = False
            return None
        return self.by_key.get(idempotency_key)

    def get_by_approval_id(self, db, *, approval_id: int):
        return self.by_approval.get(approval_id)

    def save(self, db, *, values: dict):
        self.save_count += 1
        batch = SimpleNamespace(id=51, created_at=NOW, **values)
        self.by_key[batch.idempotency_key] = batch
        self.by_approval[batch.approval_id] = batch
        self.by_id[batch.id] = batch
        return batch


class FakePaperRepository:
    def __init__(self, *, account, positions, bought_today=None, fail_on_save=None) -> None:
        self.account = account
        self.positions = {position.symbol: position for position in positions}
        self.net_filled = {position.symbol: position.quantity for position in positions}
        self.bought_today = bought_today or {}
        self.fail_on_save = fail_on_save
        self.saved = []

    def get_account_for_update(self, db, *, account_id: int):
        return self.account if account_id == self.account.id else None

    def list_positions_for_update(self, db, *, account_id: int):
        return list(self.positions.values())

    def bought_quantity_on_date(self, db, *, account_id: int, symbol: str, trade_date):
        return self.bought_today.get(symbol, 0)

    def net_filled_quantity(self, db, *, account_id: int, symbol: str):
        return self.net_filled.get(symbol, 0)

    def save_execution_fill(self, db, **kwargs):
        if self.fail_on_save == len(self.saved):
            raise RuntimeError("simulated database failure")
        fill = kwargs["fill"]
        order_record = SimpleNamespace(id=100 + len(self.saved))
        fill_record = SimpleNamespace(id=200 + len(self.saved))
        self.account.cash_balance = kwargs["cash_balance"]
        self.positions[fill.symbol] = SimpleNamespace(
            symbol=fill.symbol,
            quantity=kwargs["position_quantity"],
            average_cost=kwargs["average_cost"],
        )
        self.saved.append((order_record, fill_record))
        return order_record, fill_record


class FakeRiskService:
    def __init__(self, facts) -> None:
        self.facts = facts

    def validate_current_evaluation(self, db, *, check_id: int):
        return self.facts


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


def _risk_order(order_id: int, symbol: str, side: str, quantity: int):
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        side=side,
        order_quantity=quantity,
        reference_price=Decimal("10"),
        current_symbol_weight=Decimal("0.1"),
        projected_symbol_weight=Decimal("0.1"),
        current_industry_weight=Decimal("0.2"),
        projected_industry_weight=Decimal("0.2"),
        average_daily_amount=Decimal("1000000"),
        adv_window=20,
        adv_observation_count=20,
        rule_results=[{"rule_name": "max_symbol_weight", "passed": True}],
    )


def _setup(
    *,
    orders=None,
    cash=Decimal("100"),
    positions=None,
    execution_day_bars=None,
    daily_bar_repository=None,
    **paper_kwargs,
):
    orders = orders or [
        _risk_order(1, "AAA", "sell", -50),
        _risk_order(2, "BBB", "buy", 50),
    ]
    positions = positions or [
        SimpleNamespace(symbol="AAA", quantity=100, average_cost=Decimal("10"))
    ]
    if execution_day_bars is None:
        # Default: a tradable bar on the plan date for every order symbol so
        # legacy tests keep their existing expectations.
        from types import SimpleNamespace as _SN

        execution_day_bars = {
            order.symbol: _SN(
                symbol=order.symbol,
                open=Decimal("10"),
                close=Decimal("10"),
                high=Decimal("10"),
                low=Decimal("10"),
                pre_close=Decimal("10"),
                limit_up=Decimal("20"),
                limit_down=Decimal("1"),
                is_suspended=False,
                special_treatment_status=None,
                quality_status="trusted",
            )
            for order in orders
        }
    daily_bar_repository = daily_bar_repository or FakeDailyBarRepository(
        execution_day_bars
    )
    account = SimpleNamespace(id=1, cash_balance=cash, name="paper-main")
    check = SimpleNamespace(
        id=31,
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
    plan = SimpleNamespace(id=11, as_of_date=date(2026, 7, 21))
    targets = [
        SimpleNamespace(
            symbol=order.symbol,
            security_type="stock",
            industry="bank",
            current_quantity=100 if order.symbol == "AAA" else 0,
            target_quantity=50,
        )
        for order in orders
    ]
    equity = SimpleNamespace(id=21, cash_balance=cash, valuation_hash="v" * 64)
    summary, estimated_cash, order_hash = build_paper_order_batch(
        check=check,
        orders=orders,
        plan=plan,
        targets=targets,
        equity=equity,
        account=account,
        broker=_default_paper_broker(),
    )
    approved_at = NOW
    expires_at = approved_at + timedelta(minutes=5)
    approval_hash = build_approval_hash(
        check=check,
        equity=equity,
        order_batch_hash=order_hash,
        approved_at=approved_at,
        expires_at=expires_at,
    )
    approval = SimpleNamespace(
        id=41,
        risk_check_id=31,
        plan_id=11,
        account_id=1,
        equity_snapshot_id=21,
        status="pending",
        approval_scope="paper_execution",
        actor_type="local_single_user",
        code_version=settings.app_code_version,
        approval_hash=approval_hash,
        evaluation_hash=check.evaluation_hash,
        plan_request_hash=check.plan_request_hash,
        equity_valuation_hash=equity.valuation_hash,
        readiness_account_hash=check.readiness_account_hash,
        readiness_market_hash=check.readiness_market_hash,
        readiness_hash=check.readiness_hash,
        adv_snapshot_hash=check.adv_snapshot_hash,
        risk_policy_hash=check.risk_policy_hash,
        order_batch_hash=order_hash,
        order_summary=summary,
        confirmation_policy={
            **CONFIRMATION_POLICY,
            "estimated_cash_after": str(estimated_cash),
        },
        approved_at=approved_at,
        expires_at=expires_at,
        consumed_at=None,
    )
    execution_repository = FakeExecutionRepository()
    paper_repository = FakePaperRepository(
        account=account,
        positions=positions,
        **paper_kwargs,
    )
    service = PaperExecutionService(
        approval_repository=FakeApprovalRepository(approval),
        execution_repository=execution_repository,
        paper_repository=paper_repository,
        risk_service=FakeRiskService((check, orders, plan, targets, equity)),
        daily_bar_repository=daily_bar_repository,
    )
    return service, approval, execution_repository, paper_repository


def test_paper_execution_is_atomic_sell_first_full_fill() -> None:
    service, approval, repository, paper = _setup()

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-51",
    )

    assert result.status == "completed"
    assert [order.side for order in result.orders] == ["sell", "buy"]
    assert result.cash_after == Decimal("89.4913")
    assert paper.positions["AAA"].quantity == 50
    assert paper.positions["BBB"].quantity == 50
    assert result.real_broker_submitted is False
    assert approval.status == "consumed"
    assert repository.save_count == 1


def test_paper_execution_replays_same_idempotent_request() -> None:
    service, approval, repository, paper = _setup()
    kwargs = {
        "approval_id": 41,
        "expected_approval_hash": approval.approval_hash,
        "idempotency_key": "execute-52",
    }

    first = service.execute(FakeSession(), **kwargs)
    replay = service.execute(FakeSession(), **kwargs)

    assert replay.execution_id == first.execution_id
    assert repository.save_count == 1
    assert len(paper.saved) == 2


def test_paper_execution_rejects_same_day_buy_from_sellable_quantity() -> None:
    service, approval, _, paper = _setup(bought_today={"AAA": 60})

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-53",
    )

    assert result.status == "rejected"
    assert result.failure_code == "insufficient_available_position"
    assert result.order_count == 2
    assert result.fill_count == 0
    assert paper.saved == []
    assert paper.account.cash_balance == Decimal("100")
    assert approval.status == "consumed"


def test_paper_execution_cash_failure_writes_no_partial_fills() -> None:
    orders = [
        _risk_order(1, "AAA", "buy", 5),
        _risk_order(2, "BBB", "buy", 10),
    ]
    service, approval, _, paper = _setup(
        orders=orders,
        cash=Decimal("120"),
        positions=[],
    )

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-54",
    )

    assert result.status == "rejected"
    assert result.failure_code == "insufficient_cash"
    assert paper.saved == []
    assert paper.account.cash_balance == Decimal("120")


def test_paper_execution_write_failure_does_not_consume_approval() -> None:
    service, approval, _, _ = _setup(fail_on_save=1)

    with pytest.raises(RuntimeError, match="simulated database failure"):
        service.execute(
            FakeSession(),
            approval_id=41,
            expected_approval_hash=approval.approval_hash,
            idempotency_key="execute-55",
        )

    assert approval.status == "pending"


def test_paper_execution_blocks_unknown_position_lineage() -> None:
    service, approval, _, paper = _setup()
    paper.net_filled["AAA"] = 0

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-lineage",
    )

    assert result.status == "rejected"
    assert result.failure_code == "unknown_position_sellability"
    assert paper.saved == []


def test_paper_execution_replays_after_waiting_for_approval_lock() -> None:
    service, approval, repository, _ = _setup()
    kwargs = {
        "approval_id": 41,
        "expected_approval_hash": approval.approval_hash,
        "idempotency_key": "execute-concurrent",
    }
    first = service.execute(FakeSession(), **kwargs)
    repository.hide_next_idempotency_lookup = True

    replay = service.execute(FakeSession(), **kwargs)

    assert replay.execution_id == first.execution_id
    assert repository.save_count == 1


def test_paper_execution_rejects_changed_request_for_same_key() -> None:
    service, approval, _, _ = _setup()
    service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-56",
    )

    with pytest.raises(PaperExecutionConflict, match="different request"):
        service.execute(
            FakeSession(),
            approval_id=99,
            expected_approval_hash=approval.approval_hash,
            idempotency_key="execute-56",
        )


def _tradable_bar(
    *,
    symbol: str,
    open_: str,
    limit_up: str | None = None,
    limit_down: str | None = None,
    is_suspended: bool = False,
    pre_close: str = "10",
):
    return SimpleNamespace(
        symbol=symbol,
        open=Decimal(open_),
        close=Decimal(open_),
        high=Decimal(open_),
        low=Decimal(open_),
        pre_close=Decimal(pre_close),
        limit_up=Decimal(limit_up) if limit_up is not None else None,
        limit_down=Decimal(limit_down) if limit_down is not None else None,
        is_suspended=is_suspended,
        special_treatment_status=None,
        quality_status="trusted",
    )


def test_paper_execution_blocks_buy_at_execution_day_limit_up() -> None:

    orders = [
        _risk_order(1, "AAA", "buy", 50),
    ]
    positions = []
    # Buy reference price below limit_up; open at limit_up => buy blocked.
    risk_order = orders[0]
    risk_order.ref_price = Decimal("10")  # type: ignore[attr-defined]
    risk_order.reference_price = Decimal("10")  # type: ignore[attr-defined]
    execution_day_bars = {
        "AAA": _tradable_bar(symbol="AAA", open_="11", limit_up="11"),
    }
    service, approval, _, paper = _setup(
        orders=orders,
        cash=Decimal("1000"),
        positions=positions,
        execution_day_bars=execution_day_bars,
    )

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-tradability-buy",
    )

    assert result.status == "rejected"
    assert result.failure_code == "buy_blocked_by_execution_day_tradability"
    assert paper.saved == []


def test_paper_execution_blocks_sell_at_execution_day_limit_down() -> None:

    orders = [
        _risk_order(1, "AAA", "sell", -50),
    ]
    risk_order = orders[0]
    risk_order.reference_price = Decimal("10")  # type: ignore[attr-defined]
    execution_day_bars = {
        "AAA": _tradable_bar(
            symbol="AAA", open_="9", limit_down="9", pre_close="10",
        ),
    }
    service, approval, _, paper = _setup(
        orders=orders,
        cash=Decimal("100"),
        execution_day_bars=execution_day_bars,
    )

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-tradability-sell",
    )

    assert result.status == "rejected"
    assert result.failure_code == "sell_blocked_by_execution_day_tradability"
    assert paper.saved == []


def test_paper_execution_blocks_all_when_security_suspended_on_execution_day() -> None:

    orders = [
        _risk_order(1, "AAA", "buy", 50),
    ]
    risk_order = orders[0]
    risk_order.reference_price = Decimal("10")  # type: ignore[attr-defined]
    execution_day_bars = {
        "AAA": _tradable_bar(symbol="AAA", open_="10", is_suspended=True),
    }
    service, approval, _, paper = _setup(
        orders=orders,
        cash=Decimal("1000"),
        positions=[],
        execution_day_bars=execution_day_bars,
    )

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-tradability-suspended",
    )

    assert result.status == "rejected"
    assert result.failure_code == "buy_blocked_by_execution_day_tradability"
    assert paper.saved == []


def test_paper_execution_blocks_when_no_execution_day_bar() -> None:
    orders = [
        _risk_order(1, "AAA", "buy", 50),
    ]
    service, approval, _, paper = _setup(
        orders=orders,
        cash=Decimal("1000"),
        positions=[],
        execution_day_bars={},
    )

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-tradability-missing",
    )

    assert result.status == "rejected"
    assert result.failure_code == "missing_execution_day_bar"
    assert paper.saved == []


def test_paper_execution_executes_when_execution_day_is_tradable() -> None:
    """Smoke test: with a tradable bar the full flow still completes."""
    service, approval, _, paper = _setup()

    result = service.execute(
        FakeSession(),
        approval_id=41,
        expected_approval_hash=approval.approval_hash,
        idempotency_key="execute-tradability-ok",
    )

    assert result.status == "completed"
    assert paper.positions["AAA"].quantity == 50
    assert paper.positions["BBB"].quantity == 50
