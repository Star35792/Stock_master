from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.factor import FactorRun
from app.models.risk import RiskCheck, RiskCheckOrder
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

    def unresolved_data_quality_issues(
        self,
        db: Session,
        *,
        limit: int,
    ) -> list[DataQualityIssue]:
        return list(
            db.scalars(
                select(DataQualityIssue)
                .where(DataQualityIssue.status.in_(["open", "reopened"]))
                .order_by(DataQualityIssue.created_at.desc(), DataQualityIssue.id.desc())
                .limit(limit)
            )
        )

    def count_unresolved_data_quality_issues(self, db: Session) -> int:
        return self._count_where(
            db,
            DataQualityIssue,
            DataQualityIssue.status.in_(["open", "reopened"]),
        )

    def get_unresolved_data_quality_issue(
        self,
        db: Session,
        *,
        issue_id: int,
    ) -> DataQualityIssue | None:
        return db.scalar(
            select(DataQualityIssue).where(
                DataQualityIssue.id == issue_id,
                DataQualityIssue.status.in_(["open", "reopened"]),
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

    def failed_jobs(self, db: Session, *, limit: int) -> list[JobLog]:
        return list(
            db.scalars(
                select(JobLog)
                .where(JobLog.status.in_(["failed", "partial"]))
                .order_by(JobLog.started_at.desc(), JobLog.id.desc())
                .limit(limit)
            )
        )

    def count_failed_jobs(self, db: Session) -> int:
        return self._count_where(db, JobLog, JobLog.status.in_(["failed", "partial"]))

    def get_failed_job(self, db: Session, *, job_id: int) -> JobLog | None:
        return db.scalar(
            select(JobLog).where(
                JobLog.id == job_id,
                JobLog.status.in_(["failed", "partial"]),
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

    def failed_factor_runs(self, db: Session, *, limit: int) -> list[FactorRun]:
        return list(
            db.scalars(
                select(FactorRun)
                .where(FactorRun.status.in_(["failed", "partial"]))
                .order_by(FactorRun.started_at.desc(), FactorRun.id.desc())
                .limit(limit)
            )
        )

    def count_failed_factor_runs(self, db: Session) -> int:
        return self._count_where(
            db,
            FactorRun,
            FactorRun.status.in_(["failed", "partial"]),
        )

    def get_failed_factor_run(self, db: Session, *, run_id: int) -> FactorRun | None:
        return db.scalar(
            select(FactorRun).where(
                FactorRun.id == run_id,
                FactorRun.status.in_(["failed", "partial"]),
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

    def failed_backtest_runs(self, db: Session, *, limit: int) -> list[BacktestRun]:
        return list(
            db.scalars(
                select(BacktestRun)
                .where(BacktestRun.status == "failed")
                .order_by(BacktestRun.started_at.desc(), BacktestRun.id.desc())
                .limit(limit)
            )
        )

    def count_failed_backtest_runs(self, db: Session) -> int:
        return self._count_where(db, BacktestRun, BacktestRun.status == "failed")

    def get_failed_backtest_run(self, db: Session, *, run_id: int) -> BacktestRun | None:
        return db.scalar(
            select(BacktestRun).where(
                BacktestRun.id == run_id,
                BacktestRun.status == "failed",
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

    def count_rejected_paper_orders(self, db: Session) -> int:
        return self._count_where(db, PaperOrder, PaperOrder.status == "rejected")

    def rejected_paper_orders(self, db: Session, *, limit: int) -> list[SimpleNamespace]:
        rows = db.execute(
            select(PaperOrder, RiskCheck, RiskCheckOrder)
            .join(RiskCheck, PaperOrder.check_id == RiskCheck.id)
            .outerjoin(
                RiskCheckOrder,
                and_(
                    RiskCheckOrder.check_id == PaperOrder.check_id,
                    RiskCheckOrder.symbol == PaperOrder.symbol,
                    RiskCheckOrder.side == PaperOrder.side,
                    func.abs(RiskCheckOrder.order_quantity) == PaperOrder.order_quantity,
                ),
            )
            .where(PaperOrder.status == "rejected")
            .order_by(PaperOrder.created_at.desc(), PaperOrder.id.desc())
            .limit(limit)
        )
        return [
            SimpleNamespace(
                id=order.id,
                account_id=order.account_id,
                plan_id=order.plan_id,
                check_id=order.check_id,
                symbol=order.symbol,
                side=order.side,
                order_quantity=order.order_quantity,
                reference_price=order.reference_price,
                trade_date=order.trade_date,
                status=order.status,
                rejection_reason=order.rejection_reason,
                risk_current_drawdown=check.current_drawdown,
                risk_limits=check.limits,
                risk_code_version=check.code_version,
                risk_order_rejection_reasons=(
                    risk_order.rejection_reasons if risk_order is not None else []
                ),
                risk_order_rule_results=(
                    risk_order.rule_results if risk_order is not None else []
                ),
                created_at=order.created_at,
            )
            for order, check, risk_order in rows
        ]

    def get_rejected_paper_order(
        self,
        db: Session,
        *,
        order_id: int,
    ) -> SimpleNamespace | None:
        row = db.execute(
            select(PaperOrder, RiskCheck, RiskCheckOrder)
            .join(RiskCheck, PaperOrder.check_id == RiskCheck.id)
            .outerjoin(
                RiskCheckOrder,
                and_(
                    RiskCheckOrder.check_id == PaperOrder.check_id,
                    RiskCheckOrder.symbol == PaperOrder.symbol,
                    RiskCheckOrder.side == PaperOrder.side,
                    func.abs(RiskCheckOrder.order_quantity) == PaperOrder.order_quantity,
                ),
            )
            .where(
                PaperOrder.id == order_id,
                PaperOrder.status == "rejected",
            )
        ).first()
        if row is None:
            return None
        order, check, risk_order = row
        return SimpleNamespace(
            id=order.id,
            account_id=order.account_id,
            plan_id=order.plan_id,
            check_id=order.check_id,
            symbol=order.symbol,
            side=order.side,
            order_quantity=order.order_quantity,
            reference_price=order.reference_price,
            trade_date=order.trade_date,
            status=order.status,
            rejection_reason=order.rejection_reason,
            risk_current_drawdown=check.current_drawdown,
            risk_limits=check.limits,
            risk_code_version=check.code_version,
            risk_order_rejection_reasons=(
                risk_order.rejection_reasons if risk_order is not None else []
            ),
            risk_order_rule_results=(
                risk_order.rule_results if risk_order is not None else []
            ),
            created_at=order.created_at,
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

    @staticmethod
    def _count_where(db: Session, model: Any, condition: Any) -> int:
        return db.scalar(select(func.count()).select_from(model).where(condition)) or 0
