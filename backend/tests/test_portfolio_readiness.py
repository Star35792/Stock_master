from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.config.settings import settings
from app.portfolio import PortfolioAsset, PortfolioBuilder, PortfolioConstraints
from app.services.portfolio import hash_portfolio_snapshot
from app.services.portfolio_readiness import PortfolioRiskReadinessService

NOW = datetime(2026, 7, 21, tzinfo=UTC)
TRADE_DATE = date(2026, 7, 21)


def _snapshot() -> dict:
    return {
        "name": "draft",
        "as_of_date": TRADE_DATE.isoformat(),
        "cash": "10000",
        "assets": [
            {
                "symbol": "600000.SH",
                "security_type": "stock",
                "industry": "银行",
                "price": "100",
                "current_quantity": 0,
                "requested_weight": "0.20",
                "lot_size": 10,
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


def _plan(snapshot: dict | None = None):
    snapshot = _snapshot() if snapshot is None else snapshot
    built = PortfolioBuilder().build(
        assets=[
            PortfolioAsset(
                symbol="600000.SH",
                security_type="stock",
                industry="银行",
                price=Decimal("100"),
                current_quantity=0,
                requested_weight=Decimal("0.20"),
                lot_size=10,
            )
        ],
        cash=Decimal("10000"),
        constraints=PortfolioConstraints(),
    )
    record = SimpleNamespace(
        id=11,
        name="draft",
        as_of_date=TRADE_DATE,
        parameters={},
        total_equity=built.total_equity,
        cash_before=built.cash_before,
        target_cash=built.target_cash,
        cash_after=built.cash_after,
        code_version=settings.app_code_version,
        idempotency_key="draft-11",
        request_hash=hash_portfolio_snapshot(snapshot),
        input_snapshot=snapshot,
    )
    return record, list(built.targets)


class FakePlanRepository:
    def __init__(self, plan=None) -> None:
        self.plan, self.targets = _plan() if plan is None else plan

    def get_with_targets(self, db, *, plan_id: int):
        return (self.plan, self.targets) if plan_id == 11 else (None, [])


class FakePaperRepository:
    def __init__(self, positions=None, cash: str = "10000") -> None:
        self.account = SimpleNamespace(
            id=1,
            cash_balance=Decimal(cash),
            updated_at=NOW,
        )
        self.positions = positions or []

    def get_account(self, db, *, account_id: int):
        return self.account if account_id == 1 else None

    def list_positions(self, db, *, account_id: int):
        return self.positions


class FakeSecurityRepository:
    def __init__(self, industry: str | None = "银行") -> None:
        self.security = SimpleNamespace(
            symbol="600000.SH",
            security_type="stock",
            industry=industry,
            exchange="SH",
            is_active=True,
            list_date=date(1999, 11, 10),
            delist_date=None,
            updated_at=NOW,
        )

    def get_many(self, db, *, symbols: set[str]):
        return [self.security] if self.security.symbol in symbols else []


class FakeDailyBarRepository:
    def __init__(self, close: str = "100", quality_status: str = "trusted") -> None:
        self.bar = SimpleNamespace(
            id=7,
            symbol="600000.SH",
            trade_date=TRADE_DATE,
            adjust_type="none",
            close=Decimal(close),
            source_name="hithink",
            quality_status=quality_status,
            is_suspect=False,
            updated_at=NOW,
        )

    def list_by_symbols_on_date(self, db, **kwargs):
        return [self.bar]

    def get_latest_trusted_none_dates(self, db, *, symbols: set[str]):
        return {"600000.SH": TRADE_DATE}


class FakeCalendarRepository:
    def get_many_on_date(self, db, **kwargs):
        return [SimpleNamespace(exchange="SH", is_open=True)]


def _service(**overrides) -> PortfolioRiskReadinessService:
    return PortfolioRiskReadinessService(
        plan_repository=overrides.get("plan_repository", FakePlanRepository()),
        paper_repository=overrides.get("paper_repository", FakePaperRepository()),
        security_repository=overrides.get("security_repository", FakeSecurityRepository()),
        daily_bar_repository=overrides.get("daily_bar_repository", FakeDailyBarRepository()),
        trading_calendar_repository=overrides.get(
            "trading_calendar_repository", FakeCalendarRepository()
        ),
    )


def test_risk_readiness_accepts_matching_server_snapshots() -> None:
    result = _service().check(object(), plan_id=11, paper_account_id=1)

    assert result.ready_for_risk_check is True
    assert result.execution_allowed is False
    assert result.risk_evaluated is False
    assert result.orders_created is False
    assert result.blockers == []


def test_risk_readiness_reports_account_and_market_mismatches() -> None:
    position = SimpleNamespace(
        symbol="601398.SH",
        quantity=100,
        average_cost=Decimal("5"),
        updated_at=NOW,
    )
    result = _service(
        paper_repository=FakePaperRepository(positions=[position], cash="9000"),
        daily_bar_repository=FakeDailyBarRepository(close="99"),
    ).check(object(), plan_id=11, paper_account_id=1)

    blocker_codes = [blocker.code for blocker in result.blockers]
    assert result.ready_for_risk_check is False
    assert "account_cash_match" in blocker_codes
    assert "position_missing_from_plan" in blocker_codes
    assert "market_price_match" in blocker_codes


def test_risk_readiness_blocks_legacy_plan_without_snapshot() -> None:
    plan, targets = _plan()
    plan.input_snapshot = None
    plan.request_hash = None
    plan.idempotency_key = None
    result = _service(plan_repository=FakePlanRepository((plan, targets))).check(
        object(), plan_id=11, paper_account_id=1
    )

    assert result.ready_for_risk_check is False
    assert result.blockers[0].code == "draft_snapshot_integrity"


def test_risk_readiness_blocks_missing_industry_and_untrusted_bar() -> None:
    result = _service(
        security_repository=FakeSecurityRepository(industry=None),
        daily_bar_repository=FakeDailyBarRepository(quality_status="suspect"),
    ).check(object(), plan_id=11, paper_account_id=1)

    blocker_codes = [blocker.code for blocker in result.blockers]
    assert "security_industry_present" in blocker_codes
    assert "security_industry_match" in blocker_codes
    assert "market_bar_trusted" in blocker_codes
