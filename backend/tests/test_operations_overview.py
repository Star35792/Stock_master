from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.repositories.operations import OperationsOverviewRepository
from app.services.operations import OperationsOverviewService


class FakeOperationsOverviewRepository:
    def count_data_quality_by_status(self, db: object) -> dict[str, int]:
        return {"open": 2, "fixed": 1}

    def count_open_data_quality_by_severity(self, db: object) -> dict[str, int]:
        return {"error": 1, "warning": 1}

    def latest_data_quality_issues(
        self,
        db: object,
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=1,
                dataset="daily_bar",
                symbol="AAA",
                trade_date=date(2026, 7, 17),
                issue_type="missing_bar",
                severity="error",
                status="open",
                created_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ][:limit]

    def count_jobs_by_status(self, db: object) -> dict[str, int]:
        return {"success": 3, "failed": 1}

    def latest_jobs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=1,
                job_name="daily-sync",
                job_type="schedule",
                status="success",
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                rows_failed=0,
                error_message=None,
            )
        ][:limit]

    def count_factor_runs_by_status(self, db: object) -> dict[str, int]:
        return {"success": 2}

    def latest_factor_runs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=1,
                factor_name="ret_1d",
                factor_version="v1-none",
                status="success",
                input_start_date=date(2026, 7, 16),
                input_end_date=date(2026, 7, 17),
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                rows_written=100,
                error_message=None,
            )
        ][:limit]

    def count_backtest_runs_by_status(self, db: object) -> dict[str, int]:
        return {"success": 1}

    def latest_backtest_runs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=1,
                name="smoke",
                strategy_name="factor_score",
                status="success",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 17),
                final_equity=Decimal("101000"),
                total_return=Decimal("0.01"),
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                error_message=None,
            )
        ][:limit]

    def count_paper_accounts(self, db: object) -> int:
        return 1

    def count_paper_fills(self, db: object) -> int:
        return 2

    def count_paper_positions(self, db: object) -> int:
        return 1

    def count_paper_orders_by_status(self, db: object) -> dict[str, int]:
        return {"filled": 2}

    def latest_paper_orders(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=1,
                account_id=1,
                plan_id=1,
                check_id=1,
                symbol="AAA",
                side="buy",
                order_quantity=100,
                trade_date=date(2026, 7, 17),
                status="filled",
                rejection_reason=None,
                created_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ][:limit]

    def unresolved_data_quality_issues(
        self,
        db: object,
        *,
        limit: int,
    ) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=2,
                dataset="daily_bar",
                raw_id=12,
                symbol="AAA",
                trade_date=date(2026, 7, 17),
                adjust_type="qfq",
                source_name="hithink",
                issue_type="missing_bar",
                severity="error",
                status="open",
                description="daily bar missing",
                expected_value={"close": "required"},
                actual_value=None,
                created_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ][:limit]

    def count_unresolved_data_quality_issues(self, db: object) -> int:
        return 2

    def failed_jobs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=2,
                job_name="daily-sync",
                job_type="schedule",
                batch_id=7,
                status="failed",
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                rows_read=10,
                rows_inserted=8,
                rows_updated=0,
                rows_failed=2,
                error_message="source timeout",
                metadata_={"source_name": "hithink", "symbol": "AAA"},
            )
        ][:limit]

    def count_failed_jobs(self, db: object) -> int:
        return 1

    def count_running_jobs(self, db: object) -> int:
        return 1

    def failed_factor_runs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=2,
                factor_name="ret_1d",
                factor_version="v1-none",
                parameters={"adjust_type": "none"},
                code_version="abc123",
                input_start_date=date(2026, 7, 16),
                input_end_date=date(2026, 7, 17),
                status="failed",
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                rows_written=0,
                error_message="missing input",
                metadata_={"symbol": "AAA"},
            )
        ][:limit]

    def count_failed_factor_runs(self, db: object) -> int:
        return 1

    def failed_backtest_runs(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=2,
                name="failed-smoke",
                strategy_name="factor_score",
                parameters={"symbols": ["AAA"]},
                code_version="abc123",
                data_version="market-qfq-20260720",
                start_date=date(2026, 7, 1),
                end_date=date(2026, 7, 17),
                initial_cash=Decimal("100000"),
                final_equity=None,
                total_return=None,
                status="failed",
                started_at=datetime(2026, 7, 17, tzinfo=UTC),
                finished_at=datetime(2026, 7, 17, tzinfo=UTC),
                error_message="trusted bars insufficient",
            )
        ][:limit]

    def count_failed_backtest_runs(self, db: object) -> int:
        return 1

    def rejected_paper_orders(self, db: object, *, limit: int) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                id=2,
                account_id=1,
                plan_id=1,
                check_id=1,
                symbol="AAA",
                side="buy",
                order_quantity=100,
                reference_price=Decimal("10"),
                trade_date=date(2026, 7, 17),
                status="rejected",
                rejection_reason="risk_rejected",
                risk_current_drawdown=Decimal("0.12"),
                risk_limits={"max_drawdown": "0.10"},
                risk_code_version="risk-v1",
                risk_order_rejection_reasons=["max_drawdown_exceeded"],
                risk_order_rule_results=[
                    {
                        "rule_name": "max_drawdown",
                        "passed": False,
                        "reason_code": "max_drawdown_exceeded",
                        "message": "drawdown is above limit",
                    }
                ],
                created_at=datetime(2026, 7, 17, tzinfo=UTC),
            )
        ][:limit]

    def count_rejected_paper_orders(self, db: object) -> int:
        return 1

    def count_pending_paper_orders(self, db: object) -> int:
        return 1

    def get_unresolved_data_quality_issue(
        self,
        db: object,
        *,
        issue_id: int,
    ) -> SimpleNamespace | None:
        return self.unresolved_data_quality_issues(db, limit=1)[0] if issue_id == 2 else None

    def get_failed_job(self, db: object, *, job_id: int) -> SimpleNamespace | None:
        return self.failed_jobs(db, limit=1)[0] if job_id == 2 else None

    def get_failed_factor_run(self, db: object, *, run_id: int) -> SimpleNamespace | None:
        return self.failed_factor_runs(db, limit=1)[0] if run_id == 2 else None

    def get_failed_backtest_run(
        self,
        db: object,
        *,
        run_id: int,
    ) -> SimpleNamespace | None:
        return self.failed_backtest_runs(db, limit=1)[0] if run_id == 2 else None

    def get_rejected_paper_order(
        self,
        db: object,
        *,
        order_id: int,
    ) -> SimpleNamespace | None:
        return self.rejected_paper_orders(db, limit=1)[0] if order_id == 2 else None


def test_operations_overview_collects_dashboard_sections() -> None:
    overview = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_overview(object())

    assert overview.data_quality.issue_status_counts == {"open": 2, "fixed": 1}
    assert overview.jobs.latest_jobs[0].job_name == "daily-sync"
    assert overview.factors.latest_runs[0].factor_name == "ret_1d"
    assert overview.backtests.latest_runs[0].strategy_name == "factor_score"
    assert overview.paper_trading.account_count == 1
    assert overview.paper_trading.latest_orders[0].status == "filled"


def test_operations_failures_collects_failure_details() -> None:
    failures = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_failures(object(), limit=1)

    assert failures.data_quality_issues.total == 2
    assert failures.data_quality_issues.limit == 1
    assert failures.data_quality_issues.has_more is True
    assert failures.data_quality_issues.items[0].raw_id == 12
    assert failures.data_quality_issues.items[0].source_name == "hithink"
    assert failures.data_quality_issues.items[0].symbol == "AAA"
    assert failures.jobs.items[0].metadata == {"source_name": "hithink", "symbol": "AAA"}
    assert failures.factor_runs.items[0].metadata == {"symbol": "AAA"}
    assert failures.backtest_runs.items[0].error_message == "trusted bars insufficient"
    assert failures.paper_orders.items[0].rejection_reason == "risk_rejected"
    assert failures.paper_orders.items[0].risk_limits == {"max_drawdown": "0.10"}
    assert failures.paper_orders.items[0].risk_order_rejection_reasons == [
        "max_drawdown_exceeded"
    ]


def test_operations_failure_detail_returns_current_failure() -> None:
    detail = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_failure_detail(object(), failure_type="paper_order", failure_id=2)

    assert detail is not None
    assert detail.failure_type == "paper_order"
    assert detail.paper_order is not None
    assert detail.paper_order.risk_code_version == "risk-v1"
    assert detail.paper_order.risk_order_rule_results[0]["rule_name"] == "max_drawdown"


def test_operations_failure_detail_returns_none_for_missing_failure() -> None:
    detail = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_failure_detail(object(), failure_type="paper_order", failure_id=999)

    assert detail is None


def test_operations_alerts_collects_current_alert_candidates() -> None:
    alerts = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_alerts(object())

    assert [item.key for item in alerts.items] == [
        "data_quality_error",
        "failed_jobs",
        "running_jobs",
        "failed_factor_runs",
        "failed_backtests",
        "rejected_paper_orders",
        "pending_paper_orders",
    ]
    assert alerts.items[0].severity == "warning"
    assert alerts.items[0].count == 1
    assert alerts.items[0].rule == "open or reopened data quality issues with severity=error"
    assert alerts.items[2].severity == "info"
    assert alerts.items[4].severity == "critical"
    assert alerts.items[-1].next_action == (
        "monitor simulated order lifecycle and avoid duplicate manual actions"
    )


def test_operations_rerun_plan_maps_missing_bar_to_backfill() -> None:
    plan = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_rerun_plan(object(), failure_type="data_quality_issue", failure_id=2)

    assert plan is not None
    assert plan.supported is True
    assert plan.action_type == "daily_bar_gap_backfill"
    assert plan.parameters["symbols"] == ["AAA"]
    assert plan.parameters["adjust_type"] == "qfq"


def test_operations_rerun_plan_rejects_unsupported_adjust_type() -> None:
    class UnsupportedAdjustRepository(FakeOperationsOverviewRepository):
        def unresolved_data_quality_issues(
            self,
            db: object,
            *,
            limit: int,
        ) -> list[SimpleNamespace]:
            issue = super().unresolved_data_quality_issues(db, limit=limit)[0]
            issue.adjust_type = "hfq"
            return [issue]

    plan = OperationsOverviewService(
        repository=UnsupportedAdjustRepository()
    ).get_rerun_plan(object(), failure_type="data_quality_issue", failure_id=2)

    assert plan is not None
    assert plan.supported is False
    assert plan.action_type == "manual_review"
    assert plan.reason == "daily bar gap backfill currently supports qfq only"


def test_operations_rerun_plan_falls_back_to_manual_review() -> None:
    plan = OperationsOverviewService(
        repository=FakeOperationsOverviewRepository()
    ).get_rerun_plan(object(), failure_type="paper_order", failure_id=2)

    assert plan is not None
    assert plan.supported is False
    assert plan.action_type == "manual_review"


def test_rejected_paper_order_query_matches_signed_risk_order_quantity() -> None:
    captured: dict = {}

    class FakeDb:
        def execute(self, statement):
            captured["statement"] = statement
            return []

    OperationsOverviewRepository().rejected_paper_orders(FakeDb(), limit=1)

    sql = str(
        captured["statement"].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "abs(risk.risk_check_order.order_quantity)" in sql
    assert "trade.paper_order.order_quantity" in sql
