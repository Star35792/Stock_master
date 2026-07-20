from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.repositories.operations import OperationsOverviewRepository
from app.schemas.operations import (
    BacktestRunOverview,
    BacktestRunSnapshot,
    DataQualityIssueSnapshot,
    DataQualityOverview,
    FactorRunOverview,
    FactorRunSnapshot,
    JobLogSnapshot,
    JobOverview,
    OperationsOverview,
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
