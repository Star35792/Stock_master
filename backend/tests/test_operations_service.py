from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.operations import OperationsOverviewService


class FakeOperationsRepository:
    def __init__(
        self,
        *,
        issue_status_counts=None,
        open_severity_counts=None,
        latest_issues=None,
        job_status_counts=None,
        latest_jobs_data=None,
        factor_run_status_counts=None,
        latest_factor_runs_data=None,
        backtest_run_status_counts=None,
        latest_backtest_runs_data=None,
        paper_account_count=0,
        paper_fill_count=0,
        paper_position_count=0,
        paper_order_status_counts=None,
        latest_paper_orders_data=None,
        unresolved_issue=None,
        failed_job=None,
        failed_factor_run=None,
        failed_backtest_run=None,
        rejected_paper_order=None,
        failed_jobs_count=0,
        running_jobs_count=0,
        failed_factor_runs_count=0,
        failed_backtests_count=0,
        rejected_paper_orders_count=0,
        pending_paper_orders_count=0,
    ):
        self.issue_status_counts = issue_status_counts or {}
        self.open_severity_counts = open_severity_counts or {}
        self.latest_issues = latest_issues or []
        self.job_status_counts = job_status_counts or {}
        self._latest_jobs = latest_jobs_data or []
        self.factor_run_status_counts = factor_run_status_counts or {}
        self._latest_factor_runs = latest_factor_runs_data or []
        self.backtest_run_status_counts = backtest_run_status_counts or {}
        self._latest_backtest_runs = latest_backtest_runs_data or []
        self.paper_account_count = paper_account_count
        self.paper_fill_count = paper_fill_count
        self.paper_position_count = paper_position_count
        self.paper_order_status_counts = paper_order_status_counts or {}
        self._latest_paper_orders = latest_paper_orders_data or []
        self._unresolved_issue = unresolved_issue
        self._failed_job = failed_job
        self._failed_factor_run = failed_factor_run
        self._failed_backtest_run = failed_backtest_run
        self._rejected_paper_order = rejected_paper_order
        self.failed_jobs_count = failed_jobs_count
        self.running_jobs_count = running_jobs_count
        self.failed_factor_runs_count = failed_factor_runs_count
        self.failed_backtests_count = failed_backtests_count
        self.rejected_paper_orders_count = rejected_paper_orders_count
        self.pending_paper_orders_count = pending_paper_orders_count

    def count_data_quality_by_status(self, db):
        return self.issue_status_counts

    def count_open_data_quality_by_severity(self, db):
        return self.open_severity_counts

    def latest_data_quality_issues(self, db, *, limit):
        return self.latest_issues[:limit]

    def count_jobs_by_status(self, db):
        return self.job_status_counts

    def latest_jobs(self, db, *, limit):
        return self._latest_jobs[:limit]

    def count_factor_runs_by_status(self, db):
        return self.factor_run_status_counts

    def latest_factor_runs(self, db, *, limit):
        return self._latest_factor_runs[:limit]

    def count_backtest_runs_by_status(self, db):
        return self.backtest_run_status_counts

    def latest_backtest_runs(self, db, *, limit):
        return self._latest_backtest_runs[:limit]

    def count_paper_accounts(self, db):
        return self.paper_account_count

    def count_paper_fills(self, db):
        return self.paper_fill_count

    def count_paper_positions(self, db):
        return self.paper_position_count

    def count_paper_orders_by_status(self, db):
        return self.paper_order_status_counts

    def latest_paper_orders(self, db, *, limit):
        return self._latest_paper_orders[:limit]

    def get_unresolved_data_quality_issue(self, db, *, issue_id):
        return self._unresolved_issue

    def get_failed_job(self, db, *, job_id):
        return self._failed_job

    def get_failed_factor_run(self, db, *, run_id):
        return self._failed_factor_run

    def get_failed_backtest_run(self, db, *, run_id):
        return self._failed_backtest_run

    def get_rejected_paper_order(self, db, *, order_id):
        return self._rejected_paper_order

    def count_failed_jobs(self, db):
        return self.failed_jobs_count

    def count_running_jobs(self, db):
        return self.running_jobs_count

    def count_failed_factor_runs(self, db):
        return self.failed_factor_runs_count

    def count_failed_backtest_runs(self, db):
        return self.failed_backtests_count

    def count_rejected_paper_orders(self, db):
        return self.rejected_paper_orders_count

    def count_pending_paper_orders(self, db):
        return self.pending_paper_orders_count


def _issue(**kwargs):
    defaults = {
        "id": 1,
        "dataset": "market.daily_bar",
        "raw_id": None,
        "issue_type": "missing_bar",
        "severity": "error",
        "status": "open",
        "symbol": "AAA",
        "trade_date": datetime(2026, 7, 21, tzinfo=UTC).date(),
        "adjust_type": "qfq",
        "description": "missing",
        "source_name": "hithink",
        "expected_value": None,
        "actual_value": None,
        "created_at": datetime(2026, 7, 21, tzinfo=UTC),
        "updated_at": datetime(2026, 7, 21, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_get_overview_aggregates_repository_counts() -> None:
    repo = FakeOperationsRepository(
        issue_status_counts={"open": 3, "fixed": 1},
        open_severity_counts={"critical": 1, "error": 2},
        job_status_counts={"success": 5, "failed": 1},
        factor_run_status_counts={"success": 4},
        backtest_run_status_counts={"success": 2},
        paper_account_count=2,
        paper_fill_count=10,
        paper_position_count=3,
        paper_order_status_counts={"completed": 5},
    )
    service = OperationsOverviewService(repository=repo)

    overview = service.get_overview(None, limit=0)

    assert overview.data_quality.issue_status_counts == {"open": 3, "fixed": 1}
    assert overview.jobs.status_counts == {"success": 5, "failed": 1}
    assert overview.paper_trading.account_count == 2
    assert len(overview.data_quality.latest_issues) == 0


def test_get_alerts_includes_critical_and_error_issues() -> None:
    repo = FakeOperationsRepository(
        open_severity_counts={"critical": 1, "error": 3},
        failed_jobs_count=2,
        failed_factor_runs_count=1,
        failed_backtests_count=1,
        rejected_paper_orders_count=4,
        pending_paper_orders_count=1,
    )
    service = OperationsOverviewService(repository=repo)

    alerts = service.get_alerts(None)

    keys = {item.key for item in alerts.items}
    assert "data_quality_critical" in keys
    assert "data_quality_error" in keys
    assert "failed_jobs" in keys
    assert "failed_backtests" in keys
    assert "rejected_paper_orders" in keys
    assert "pending_paper_orders" in keys


def test_get_failure_detail_returns_data_quality_issue() -> None:
    repo = FakeOperationsRepository(unresolved_issue=_issue(id=7))
    service = OperationsOverviewService(repository=repo)

    detail = service.get_failure_detail(None, failure_type="data_quality_issue", failure_id=7)

    assert detail is not None
    assert detail.failure_type == "data_quality_issue"
    assert detail.data_quality_issue.id == 7


def test_get_failure_detail_returns_none_for_unknown_type() -> None:
    service = OperationsOverviewService(repository=FakeOperationsRepository())

    with pytest.raises(ValueError, match="unsupported failure_type"):
        service.get_failure_detail(None, failure_type="unknown", failure_id=1)


def test_get_rerun_plan_supports_missing_bar_gap_backfill() -> None:
    repo = FakeOperationsRepository(
        unresolved_issue=_issue(
            id=8,
            issue_type="missing_bar",
            symbol="AAA",
            trade_date=datetime(2026, 7, 21, tzinfo=UTC).date(),
            adjust_type="qfq",
        )
    )
    service = OperationsOverviewService(repository=repo)

    plan = service.get_rerun_plan(None, failure_type="data_quality_issue", failure_id=8)

    assert plan is not None
    assert plan.supported is True
    assert plan.action_type == "daily_bar_gap_backfill"
    assert plan.parameters["symbols"] == ["AAA"]


def test_get_rerun_plan_rejects_unsupported_adjust_type() -> None:
    repo = FakeOperationsRepository(
        unresolved_issue=_issue(
            id=9,
            issue_type="missing_bar",
            symbol="AAA",
            trade_date=datetime(2026, 7, 21, tzinfo=UTC).date(),
            adjust_type="hfq",
        )
    )
    service = OperationsOverviewService(repository=repo)

    plan = service.get_rerun_plan(None, failure_type="data_quality_issue", failure_id=9)

    assert plan.supported is False
    assert plan.action_type == "manual_review"


def test_get_rerun_plan_returns_none_when_detail_missing() -> None:
    service = OperationsOverviewService(repository=FakeOperationsRepository())

    plan = service.get_rerun_plan(None, failure_type="data_quality_issue", failure_id=999)
    assert plan is None
