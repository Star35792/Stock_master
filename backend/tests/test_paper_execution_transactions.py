from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import settings
from app.database.base import Base
from app.database.session import SCHEMAS
from app.models.portfolio import PortfolioPlanRecord, PortfolioTargetRecord
from app.models.risk import RiskCheck, RiskCheckOrder
from app.models.trade import (
    PaperAccount,
    PaperAccountEquitySnapshot,
    PaperExecutionApproval,
    PaperExecutionBatch,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from app.repositories.trade import (
    PaperExecutionApprovalRepository,
    PaperTradingRepository,
)
from app.services.paper_approval import (
    CONFIRMATION_POLICY,
    _default_paper_broker,
    build_approval_hash,
    build_paper_order_batch,
)
from app.services.paper_execution import PaperExecutionService
from app.services.snapshot_hashes import hash_payload


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory):
    # A file-based DuckDB is required here: these tests open multiple
    # concurrent sessions (and threads), which an in-memory database cannot
    # share across connections.
    db_path = tmp_path_factory.mktemp("paper_exec") / "test.duckdb"
    engine = create_engine(f"duckdb:///{db_path}")
    with engine.connect() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


class DatabaseRiskService:
    def validate_current_evaluation(self, db: Session, *, check_id: int):
        check = db.get(RiskCheck, check_id)
        orders = list(
            db.scalars(
                select(RiskCheckOrder)
                .where(RiskCheckOrder.check_id == check_id)
                .order_by(RiskCheckOrder.symbol)
            )
        )
        plan = db.get(PortfolioPlanRecord, check.plan_id)
        targets = list(
            db.scalars(
                select(PortfolioTargetRecord)
                .where(PortfolioTargetRecord.plan_id == plan.id)
                .order_by(PortfolioTargetRecord.symbol)
            )
        )
        equity = db.get(PaperAccountEquitySnapshot, check.equity_snapshot_id)
        return check, orders, plan, targets, equity


class FailOnSecondFillRepository(PaperTradingRepository):
    def __init__(self) -> None:
        self.count = 0

    def save_execution_fill(self, db: Session, **kwargs):
        result = super().save_execution_fill(db, **kwargs)
        self.count += 1
        if self.count == 2:
            raise RuntimeError("fail after second fill flush")
        return result


class FakeDailyBarRepository:
    """Provides a tradable bar so the new execution-day check passes.

    The integration test does not seed ``market.daily_bar``; without this
    fake the execution would short-circuit on a missing execution-day bar
    before reaching the second fill failure the test is asserting.
    """

    def list_by_symbols_on_date(
        self,
        db: Session,
        *,
        symbols,
        trade_date,
        adjust_type,
    ):
        from types import SimpleNamespace as _SN

        return [
            _SN(
                symbol=symbol,
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
            for symbol in sorted(symbols)
        ]


class BarrierApprovalRepository(PaperExecutionApprovalRepository):
    def __init__(self, barrier: Barrier) -> None:
        self.barrier = barrier
        self._synced = False

    def get_for_update(self, db: Session, *, approval_id: int):
        # The barrier only forces the initial simultaneous collision. DuckDB's
        # optimistic-concurrency loser retries execute(), which re-enters this
        # method; waiting again would deadlock against the winner that has
        # already moved on, so we synchronize exactly once.
        if not self._synced:
            self._synced = True
            self.barrier.wait(timeout=10)
        return super().get_for_update(db, approval_id=approval_id)


def _seed(session_factory):
    suffix = uuid4().hex
    trade_date = date(2026, 7, 21)
    with session_factory() as db:
        account = PaperAccount(name=f"paper-{suffix}", cash_balance=Decimal("100"))
        db.add(account)
        db.flush()
        plan = PortfolioPlanRecord(
            name=f"plan-{suffix}",
            as_of_date=trade_date,
            parameters={},
            total_equity=Decimal("1100"),
            cash_before=Decimal("100"),
            target_cash=Decimal("99.5"),
            cash_after=Decimal("99.5"),
            code_version=settings.app_code_version,
            idempotency_key=f"plan-{suffix}",
            request_hash="p" * 64,
            input_snapshot={},
        )
        db.add(plan)
        db.flush()
        targets = [
            PortfolioTargetRecord(
                plan_id=plan.id,
                symbol="AAA",
                security_type="stock",
                industry="bank",
                requested_weight=Decimal("0.5"),
                constrained_weight=Decimal("0.5"),
                final_weight=Decimal("0.5"),
                current_quantity=100,
                target_quantity=50,
                order_quantity=-50,
                price=Decimal("10"),
                reasons=[],
            ),
            PortfolioTargetRecord(
                plan_id=plan.id,
                symbol="BBB",
                security_type="stock",
                industry="bank",
                requested_weight=Decimal("0.5"),
                constrained_weight=Decimal("0.5"),
                final_weight=Decimal("0.5"),
                current_quantity=0,
                target_quantity=50,
                order_quantity=50,
                price=Decimal("10"),
                reasons=[],
            ),
        ]
        db.add_all(targets)
        equity = PaperAccountEquitySnapshot(
            account_id=account.id,
            valuation_date=trade_date,
            cash_balance=Decimal("100"),
            positions_market_value=Decimal("1000"),
            total_equity=Decimal("1100"),
            high_watermark=Decimal("1100"),
            drawdown=Decimal("0"),
            previous_snapshot_id=None,
            monitoring_start_date=trade_date,
            observation_count=20,
            history_status="continuous",
            account_snapshot_hash="a" * 64,
            market_snapshot_hash="m" * 64,
            valuation_hash="v" * 64,
            valuation_policy={},
            code_version=settings.app_code_version,
            idempotency_key=f"equity-{suffix}",
            request_hash="u" * 64,
        )
        db.add(equity)
        db.flush()
        check = RiskCheck(
            plan_id=plan.id,
            current_drawdown=Decimal("0"),
            limits={},
            passed=True,
            code_version=settings.app_code_version,
            account_id=account.id,
            equity_snapshot_id=equity.id,
            plan_request_hash=plan.request_hash,
            readiness_account_hash="a" * 64,
            readiness_market_hash="m" * 64,
            readiness_hash="r" * 64,
            readiness_snapshot={},
            adv_snapshot_hash="d" * 64,
            adv_inputs={},
            risk_policy_hash="q" * 64,
            evaluation_hash=hash_payload({"suffix": suffix}),
            evaluation_status="passed",
            idempotency_key=f"risk-{suffix}",
            request_hash="k" * 64,
        )
        db.add(check)
        db.flush()
        risk_orders = [
            RiskCheckOrder(
                check_id=check.id,
                symbol=target.symbol,
                side="sell" if target.order_quantity < 0 else "buy",
                order_quantity=target.order_quantity,
                passed=True,
                rejection_reasons=[],
                rule_results=[{"rule_name": "test", "passed": True}],
                reference_price=target.price,
                current_symbol_weight=Decimal("0.1"),
                projected_symbol_weight=Decimal("0.1"),
                current_industry_weight=Decimal("0.2"),
                projected_industry_weight=Decimal("0.2"),
                average_daily_amount=Decimal("1000000"),
                adv_window=20,
                adv_observation_count=20,
                adv_input_hash="i" * 64,
            )
            for target in targets
        ]
        db.add_all(risk_orders)
        db.flush()
        position = PaperPosition(
            account_id=account.id,
            symbol="AAA",
            quantity=100,
            average_cost=Decimal("10"),
        )
        db.add(position)
        lineage_order = PaperOrder(
            account_id=account.id,
            plan_id=plan.id,
            check_id=check.id,
            symbol="AAA",
            side="buy",
            order_quantity=100,
            reference_price=Decimal("10"),
            trade_date=trade_date - timedelta(days=1),
            status="filled",
            rejection_reason=None,
        )
        db.add(lineage_order)
        db.flush()
        db.add(
            PaperFill(
                order_id=lineage_order.id,
                account_id=account.id,
                symbol="AAA",
                side="buy",
                quantity=100,
                price=Decimal("10"),
                commission=Decimal("0"),
                trade_date=trade_date - timedelta(days=1),
            )
        )
        summary, estimated_cash, order_hash = build_paper_order_batch(
            check=check,
            orders=risk_orders,
            plan=plan,
            targets=targets,
            equity=equity,
            account=account,
            broker=_default_paper_broker(),
        )
        approved_at = datetime.now(UTC)
        expires_at = approved_at + timedelta(minutes=5)
        approval = PaperExecutionApproval(
            risk_check_id=check.id,
            plan_id=plan.id,
            account_id=account.id,
            equity_snapshot_id=equity.id,
            status="pending",
            approval_scope="paper_execution",
            actor_type="local_single_user",
            approved_by="local_operator",
            code_version=settings.app_code_version,
            evaluation_hash=check.evaluation_hash,
            plan_request_hash=check.plan_request_hash,
            equity_valuation_hash=equity.valuation_hash,
            readiness_account_hash=check.readiness_account_hash,
            readiness_market_hash=check.readiness_market_hash,
            readiness_hash=check.readiness_hash,
            adv_snapshot_hash=check.adv_snapshot_hash,
            risk_policy_hash=check.risk_policy_hash,
            order_batch_hash=order_hash,
            approval_hash=build_approval_hash(
                check=check,
                equity=equity,
                order_batch_hash=order_hash,
                approved_at=approved_at,
                expires_at=expires_at,
            ),
            order_summary=summary,
            confirmation_policy={
                **CONFIRMATION_POLICY,
                "estimated_cash_after": str(estimated_cash),
            },
            approved_at=approved_at,
            expires_at=expires_at,
            idempotency_key=f"approval-{suffix}",
            request_hash="j" * 64,
        )
        db.add(approval)
        db.commit()
        return approval.id, approval.approval_hash, account.id


def test_rolls_back_second_fill_failure(session_factory) -> None:
    approval_id, approval_hash, account_id = _seed(session_factory)
    service = PaperExecutionService(
        paper_repository=FailOnSecondFillRepository(),
        risk_service=DatabaseRiskService(),
        daily_bar_repository=FakeDailyBarRepository(),
    )

    with session_factory() as db, pytest.raises(RuntimeError, match="second fill"):
        service.execute(
            db,
            approval_id=approval_id,
            expected_approval_hash=approval_hash,
            idempotency_key=f"execute-{uuid4().hex}",
        )
        db.commit()
    with session_factory() as db:
        approval = db.get(PaperExecutionApproval, approval_id)
        account = db.get(PaperAccount, account_id)
        position = db.scalar(
            select(PaperPosition).where(
                PaperPosition.account_id == account_id,
                PaperPosition.symbol == "AAA",
            )
        )
        assert approval.status == "pending"
        assert account.cash_balance == Decimal("100.0000")
        assert position.quantity == 100
        assert (
            db.scalar(
                select(func.count(PaperExecutionBatch.id)).where(
                    PaperExecutionBatch.approval_id == approval_id
                )
            )
            == 0
        )
        assert (
            db.scalar(select(func.count(PaperOrder.id)).where(PaperOrder.account_id == account_id))
            == 1
        )
        assert (
            db.scalar(select(func.count(PaperFill.id)).where(PaperFill.account_id == account_id))
            == 1
        )


def test_concurrent_same_key_replays_one_batch(session_factory) -> None:
    approval_id, approval_hash, _ = _seed(session_factory)
    barrier = Barrier(2)
    key = f"execute-{uuid4().hex}"

    def run_execution():
        service = PaperExecutionService(
            approval_repository=BarrierApprovalRepository(barrier),
            risk_service=DatabaseRiskService(),
            daily_bar_repository=FakeDailyBarRepository(),
        )
        with session_factory() as db:
            result = service.execute(
                db,
                approval_id=approval_id,
                expected_approval_hash=approval_hash,
                idempotency_key=key,
            )
            db.commit()
            return result.execution_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        execution_ids = list(executor.map(lambda _: run_execution(), range(2)))

    assert execution_ids[0] == execution_ids[1]
    with session_factory() as db:
        assert (
            db.scalar(
                select(func.count(PaperExecutionBatch.id)).where(
                    PaperExecutionBatch.approval_id == approval_id
                )
            )
            == 1
        )


def test_rejected_batch_requires_complete_before_after_state(
    session_factory,
) -> None:
    approval_id, _, _ = _seed(session_factory)
    now = datetime.now(UTC)
    with session_factory() as db:
        approval = db.get(PaperExecutionApproval, approval_id)
        db.add(
            PaperExecutionBatch(
                approval_id=approval.id,
                risk_check_id=approval.risk_check_id,
                plan_id=approval.plan_id,
                account_id=approval.account_id,
                equity_snapshot_id=approval.equity_snapshot_id,
                status="rejected",
                execution_mode="paper",
                actor_type="local_single_user",
                executed_by="local_operator",
                code_version=settings.app_code_version,
                approval_hash=approval.approval_hash,
                evaluation_hash=approval.evaluation_hash,
                order_batch_hash=approval.order_batch_hash,
                execution_policy_hash="x" * 64,
                account_state_hash_before="a" * 64,
                account_state_hash_after=None,
                execution_hash="z" * 64,
                cash_before=None,
                cash_after=None,
                order_count=len(approval.order_summary),
                fill_count=0,
                total_commission=Decimal(),
                input_snapshot=approval.order_summary,
                result_snapshot=[],
                execution_policy={},
                failure_code="test_rejection",
                failure_message="constraint smoke",
                idempotency_key=f"constraint-{uuid4().hex}",
                request_hash="w" * 64,
                started_at=now,
                completed_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
