"""Alert candidates describe the newest state of each logical run series."""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.research_base import ResearchBase
from app.models.factor import FactorRun
from app.models.strategy import BacktestRun, TrainingArtifact
from app.models.system import JobLog
from app.repositories.operations import OperationsOverviewRepository


def _started(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def _backtest(name: str, status: str, day: int) -> BacktestRun:
    return BacktestRun(
        name=name,
        strategy_name="factor_score",
        parameters={},
        code_version="test",
        data_version="test",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 2, 1),
        initial_cash=Decimal("100000"),
        status=status,
        started_at=_started(day),
    )


def test_alert_counts_ignore_superseded_failures_but_keep_history() -> None:
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        for schema in ("metadata", "factor", "strategy"):
            connection.execute(text(f"CREATE SCHEMA {schema}"))
    Base.metadata.create_all(engine, tables=[JobLog.__table__, FactorRun.__table__])
    ResearchBase.metadata.create_all(
        engine, tables=[TrainingArtifact.__table__, BacktestRun.__table__]
    )
    factory = sessionmaker(bind=engine)
    repository = OperationsOverviewRepository()
    with factory() as db:
        db.add_all(
            [
                JobLog(
                    job_name="daily_loop", job_type="schedule", status="failed",
                    started_at=_started(1),
                ),
                JobLog(
                    job_name="daily_loop", job_type="schedule", status="success",
                    started_at=_started(2),
                ),
                JobLog(
                    job_name="input_prep", job_type="schedule", status="partial",
                    started_at=_started(3),
                ),
                FactorRun(
                    definition_id=1, factor_name="momentum", factor_version="v1",
                    code_version="test", status="failed", started_at=_started(1),
                ),
                FactorRun(
                    definition_id=1, factor_name="momentum", factor_version="v1",
                    code_version="test", status="success", started_at=_started(2),
                    rows_written=10,
                ),
                FactorRun(
                    definition_id=2, factor_name="value", factor_version="v1",
                    code_version="test", status="partial", started_at=_started(3),
                ),
                _backtest("study-a", "failed", 1),
                _backtest("study-a", "success", 2),
                _backtest("study-b", "failed", 3),
            ]
        )
        db.commit()

        assert repository.count_failed_jobs(db) == 2
        assert repository.count_latest_failed_jobs(db) == 1
        assert repository.count_failed_factor_runs(db) == 2
        assert repository.count_latest_failed_factor_runs(db) == 1
        assert repository.count_failed_backtest_runs(db) == 2
        assert repository.count_latest_failed_backtest_runs(db) == 1

    engine.dispose()
