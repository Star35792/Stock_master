from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.factor import FactorRun
from app.models.strategy import BacktestRun
from app.models.system import DataQualityIssue, JobLog
from app.models.trade import PaperAccount, PaperFill, PaperOrder, PaperPosition


class OperationsOverviewRepository:
    def count_data_quality_by_status(self, db: Session) -> dict[str, int]:
        return self._count_by(db, DataQualityIssue.status)

    def count_open_data_quality_by_severity(self, db: Session) -> dict[str, int]:
        rows = db.execute(
            select(DataQualityIssue.severity, func.count())
            .where(DataQualityIssue.status.in_(["open", "reopened"]))
            .group_by(DataQualityIssue.severity)
        )
        return {status: count for status, count in rows}

    def latest_data_quality_issues(
        self,
        db: Session,
        *,
        limit: int,
    ) -> list[DataQualityIssue]:
        return list(
            db.scalars(
                select(DataQualityIssue)
                .order_by(DataQualityIssue.created_at.desc(), DataQualityIssue.id.desc())
                .limit(limit)
            )
        )

    def count_jobs_by_status(self, db: Session) -> dict[str, int]:
        return self._count_by(db, JobLog.status)

    def latest_jobs(self, db: Session, *, limit: int) -> list[JobLog]:
        return list(
            db.scalars(
                select(JobLog).order_by(JobLog.started_at.desc(), JobLog.id.desc()).limit(limit)
            )
        )

    def count_factor_runs_by_status(self, db: Session) -> dict[str, int]:
        return self._count_by(db, FactorRun.status)

    def latest_factor_runs(self, db: Session, *, limit: int) -> list[FactorRun]:
        return list(
            db.scalars(
                select(FactorRun)
                .order_by(FactorRun.started_at.desc(), FactorRun.id.desc())
                .limit(limit)
            )
        )

    def count_backtest_runs_by_status(self, db: Session) -> dict[str, int]:
        return self._count_by(db, BacktestRun.status)

    def latest_backtest_runs(self, db: Session, *, limit: int) -> list[BacktestRun]:
        return list(
            db.scalars(
                select(BacktestRun)
                .order_by(BacktestRun.started_at.desc(), BacktestRun.id.desc())
                .limit(limit)
            )
        )

    def count_paper_orders_by_status(self, db: Session) -> dict[str, int]:
        return self._count_by(db, PaperOrder.status)

    def latest_paper_orders(self, db: Session, *, limit: int) -> list[PaperOrder]:
        return list(
            db.scalars(
                select(PaperOrder)
                .order_by(PaperOrder.created_at.desc(), PaperOrder.id.desc())
                .limit(limit)
            )
        )

    def count_paper_accounts(self, db: Session) -> int:
        return self._count_rows(db, PaperAccount)

    def count_paper_fills(self, db: Session) -> int:
        return self._count_rows(db, PaperFill)

    def count_paper_positions(self, db: Session) -> int:
        return self._count_rows(db, PaperPosition)

    @staticmethod
    def _count_by(db: Session, column: Any) -> dict[str, int]:
        rows = db.execute(select(column, func.count()).group_by(column))
        return {status: count for status, count in rows}

    @staticmethod
    def _count_rows(db: Session, model: Any) -> int:
        return db.scalar(select(func.count()).select_from(model)) or 0
