from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

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
