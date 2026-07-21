from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.repositories.operations import OperationsOverviewRepository
from app.schemas.operations import (
    BacktestRunFailure,
    BacktestRunFailureSection,
    BacktestRunOverview,
    BacktestRunSnapshot,
    DataQualityFailure,
    DataQualityFailureSection,
    DataQualityIssueSnapshot,
    DataQualityOverview,
    FactorRunFailure,
    FactorRunFailureSection,
    FactorRunOverview,
    FactorRunSnapshot,
    JobFailure,
    JobFailureSection,
    JobLogSnapshot,
    JobOverview,
    OperationsAlertItem,
    OperationsAlertSummary,
    OperationsFailureDetail,
    OperationsFailureReport,
    OperationsOverview,
    OperationsRerunPlan,
    PaperOrderFailure,
    PaperOrderFailureSection,
    PaperOrderSnapshot,
    PaperTradingOverview,
)


class OperationsOverviewService:
    def __init__(
        self,
        repository: OperationsOverviewRepository | None = None,
    ) -> None:
        self.repository = repository or OperationsOverviewRepository()

    def get_overview(self, db: Session, *, limit: int = 5) -> OperationsOverview:
        return OperationsOverview(
            generated_at=datetime.now(UTC),
            data_quality=DataQualityOverview(
                issue_status_counts=self.repository.count_data_quality_by_status(db),
                open_issue_severity_counts=(
                    self.repository.count_open_data_quality_by_severity(db)
                ),
                latest_issues=[
                    DataQualityIssueSnapshot.model_validate(issue)
                    for issue in self.repository.latest_data_quality_issues(
                        db,
                        limit=limit,
                    )
                ],
            ),
            jobs=JobOverview(
                status_counts=self.repository.count_jobs_by_status(db),
                latest_jobs=[
                    JobLogSnapshot.model_validate(job)
                    for job in self.repository.latest_jobs(db, limit=limit)
                ],
            ),
            factors=FactorRunOverview(
                status_counts=self.repository.count_factor_runs_by_status(db),
                latest_runs=[
                    FactorRunSnapshot.model_validate(run)
                    for run in self.repository.latest_factor_runs(db, limit=limit)
                ],
            ),
            backtests=BacktestRunOverview(
                status_counts=self.repository.count_backtest_runs_by_status(db),
                latest_runs=[
                    BacktestRunSnapshot.model_validate(run)
                    for run in self.repository.latest_backtest_runs(db, limit=limit)
                ],
            ),
            paper_trading=PaperTradingOverview(
                account_count=self.repository.count_paper_accounts(db),
                fill_count=self.repository.count_paper_fills(db),
                position_count=self.repository.count_paper_positions(db),
                order_status_counts=self.repository.count_paper_orders_by_status(db),
                latest_orders=[
                    PaperOrderSnapshot.model_validate(order)
                    for order in self.repository.latest_paper_orders(db, limit=limit)
                ],
            ),
        )

    def get_failure_detail(
        self,
        db: Session,
        *,
        failure_type: str,
        failure_id: int,
    ) -> OperationsFailureDetail | None:
        if failure_type == "data_quality_issue":
            issue = self.repository.get_unresolved_data_quality_issue(
                db,
                issue_id=failure_id,
            )
            return (
                None
                if issue is None
                else OperationsFailureDetail(
                    failure_type="data_quality_issue",
                    data_quality_issue=DataQualityFailure.model_validate(issue),
                )
            )
        if failure_type == "job":
            job = self.repository.get_failed_job(db, job_id=failure_id)
            return (
                None
                if job is None
                else OperationsFailureDetail(
                    failure_type="job",
                    job=JobFailure.model_validate(job),
                )
            )
        if failure_type == "factor_run":
            run = self.repository.get_failed_factor_run(db, run_id=failure_id)
            return (
                None
                if run is None
                else OperationsFailureDetail(
                    failure_type="factor_run",
                    factor_run=FactorRunFailure.model_validate(run),
                )
            )
        if failure_type == "backtest_run":
            run = self.repository.get_failed_backtest_run(db, run_id=failure_id)
            return (
                None
                if run is None
                else OperationsFailureDetail(
                    failure_type="backtest_run",
                    backtest_run=BacktestRunFailure.model_validate(run),
                )
            )
        if failure_type == "paper_order":
            order = self.repository.get_rejected_paper_order(db, order_id=failure_id)
            return (
                None
                if order is None
                else OperationsFailureDetail(
                    failure_type="paper_order",
                    paper_order=PaperOrderFailure.model_validate(order),
                )
            )
        raise ValueError("unsupported failure_type")

    def get_alerts(self, db: Session) -> OperationsAlertSummary:
        open_severity_counts = self.repository.count_open_data_quality_by_severity(db)
        alert_items = []
        critical_count = open_severity_counts.get("critical", 0)
        if critical_count > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="data_quality_critical",
                    severity="critical",
                    count=critical_count,
                    title="Critical data quality issues",
                    detail="Unresolved critical data quality issues require review",
                    rule="open or reopened data quality issues with severity=critical",
                    next_action=(
                        "open the failures center and review data quality issue details"
                    ),
                )
            )
        error_count = open_severity_counts.get("error", 0)
        if error_count > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="data_quality_error",
                    severity="warning",
                    count=error_count,
                    title="Data quality errors",
                    detail="Open error-level data quality issues remain unresolved",
                    rule="open or reopened data quality issues with severity=error",
                    next_action="inspect affected dataset, symbol, trade_date, and rerun plan",
                )
            )
        failed_jobs = self.repository.count_failed_jobs(db)
        if failed_jobs > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="failed_jobs",
                    severity="warning",
                    count=failed_jobs,
                    title="Failed jobs",
                    detail="Batch and scheduler jobs reported failures or partial failures",
                    rule="job logs with status in failed or partial",
                    next_action="review failed rows, error message, and source metadata",
                )
            )
        running_jobs = self.repository.count_running_jobs(db)
        if running_jobs > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="running_jobs",
                    severity="info",
                    count=running_jobs,
                    title="Running jobs",
                    detail="Batch or scheduler jobs are currently running",
                    rule="job logs with status=running",
                    next_action="monitor progress before deciding whether manual review is needed",
                )
            )
        failed_factor_runs = self.repository.count_failed_factor_runs(db)
        if failed_factor_runs > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="failed_factor_runs",
                    severity="warning",
                    count=failed_factor_runs,
                    title="Failed factor runs",
                    detail="Factor runs ended in failed or partial status",
                    rule="factor runs with status in failed or partial",
                    next_action="review factor parameters, input window, and upstream data quality",
                )
            )
        failed_backtests = self.repository.count_failed_backtest_runs(db)
        if failed_backtests > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="failed_backtests",
                    severity="critical",
                    count=failed_backtests,
                    title="Failed backtests",
                    detail="Backtest runs ended in failed status",
                    rule="backtest runs with status=failed",
                    next_action="inspect run detail, data_version, parameters, and error message",
                )
            )
        rejected_orders = self.repository.count_rejected_paper_orders(db)
        if rejected_orders > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="rejected_paper_orders",
                    severity="warning",
                    count=rejected_orders,
                    title="Rejected paper orders",
                    detail="Paper trading orders were rejected by risk or account checks",
                    rule="paper orders with status=rejected",
                    next_action="review risk rule results before changing any portfolio inputs",
                )
            )
        pending_orders = self.repository.count_pending_paper_orders(db)
        if pending_orders > 0:
            alert_items.append(
                OperationsAlertItem(
                    key="pending_paper_orders",
                    severity="info",
                    count=pending_orders,
                    title="Pending paper orders",
                    detail="Paper trading orders are waiting for fill or cancellation",
                    rule="paper orders with status=pending",
                    next_action=(
                        "monitor simulated order lifecycle and avoid duplicate manual actions"
                    ),
                )
            )
        return OperationsAlertSummary(
            generated_at=datetime.now(UTC),
            items=alert_items,
        )

    def get_rerun_plan(
        self,
        db: Session,
        *,
        failure_type: str,
        failure_id: int,
    ) -> OperationsRerunPlan | None:
        detail = self.get_failure_detail(
            db,
            failure_type=failure_type,
            failure_id=failure_id,
        )
        if detail is None:
            return None
        if detail.data_quality_issue is not None:
            return self._data_quality_rerun_plan(
                failure_type=failure_type,
                failure_id=failure_id,
                issue=detail.data_quality_issue,
            )
        return OperationsRerunPlan(
            failure_type=failure_type,  # type: ignore[arg-type]
            failure_id=failure_id,
            supported=False,
            action_type="manual_review",
            reason="no safe rerun action is registered for this failure type",
            parameters={},
        )

    @staticmethod
    def _data_quality_rerun_plan(
        *,
        failure_type: str,
        failure_id: int,
        issue: DataQualityFailure,
    ) -> OperationsRerunPlan:
        if (
            issue.issue_type == "missing_bar"
            and issue.symbol is not None
            and issue.trade_date is not None
        ):
            if issue.adjust_type not in {None, "qfq"}:
                return OperationsRerunPlan(
                    failure_type=failure_type,  # type: ignore[arg-type]
                    failure_id=failure_id,
                    supported=False,
                    action_type="manual_review",
                    reason="daily bar gap backfill currently supports qfq only",
                    parameters={},
                )
            return OperationsRerunPlan(
                failure_type=failure_type,  # type: ignore[arg-type]
                failure_id=failure_id,
                supported=True,
                action_type="daily_bar_gap_backfill",
                reason="missing daily bar can be backfilled for the affected symbol/date",
                parameters={
                    "symbols": [issue.symbol],
                    "start_date": issue.trade_date.isoformat(),
                    "end_date": issue.trade_date.isoformat(),
                    "adjust_type": issue.adjust_type or "qfq",
                },
            )
        return OperationsRerunPlan(
            failure_type=failure_type,  # type: ignore[arg-type]
            failure_id=failure_id,
            supported=False,
            action_type="manual_review",
            reason="data quality issue does not contain enough safe rerun context",
            parameters={},
        )

    def get_failures(self, db: Session, *, limit: int = 20) -> OperationsFailureReport:
        data_quality_total = self.repository.count_unresolved_data_quality_issues(db)
        jobs_total = self.repository.count_failed_jobs(db)
        factor_runs_total = self.repository.count_failed_factor_runs(db)
        backtest_runs_total = self.repository.count_failed_backtest_runs(db)
        paper_orders_total = self.repository.count_rejected_paper_orders(db)
        return OperationsFailureReport(
            generated_at=datetime.now(UTC),
            data_quality_issues=DataQualityFailureSection(
                total=data_quality_total,
                limit=limit,
                has_more=data_quality_total > limit,
                items=[
                    DataQualityFailure.model_validate(issue)
                    for issue in self.repository.unresolved_data_quality_issues(
                        db,
                        limit=limit,
                    )
                ],
            ),
            jobs=JobFailureSection(
                total=jobs_total,
                limit=limit,
                has_more=jobs_total > limit,
                items=[
                    JobFailure.model_validate(job)
                    for job in self.repository.failed_jobs(db, limit=limit)
                ],
            ),
            factor_runs=FactorRunFailureSection(
                total=factor_runs_total,
                limit=limit,
                has_more=factor_runs_total > limit,
                items=[
                    FactorRunFailure.model_validate(run)
                    for run in self.repository.failed_factor_runs(db, limit=limit)
                ],
            ),
            backtest_runs=BacktestRunFailureSection(
                total=backtest_runs_total,
                limit=limit,
                has_more=backtest_runs_total > limit,
                items=[
                    BacktestRunFailure.model_validate(run)
                    for run in self.repository.failed_backtest_runs(db, limit=limit)
                ],
            ),
            paper_orders=PaperOrderFailureSection(
                total=paper_orders_total,
                limit=limit,
                has_more=paper_orders_total > limit,
                items=[
                    PaperOrderFailure.model_validate(order)
                    for order in self.repository.rejected_paper_orders(db, limit=limit)
                ],
            ),
        )
