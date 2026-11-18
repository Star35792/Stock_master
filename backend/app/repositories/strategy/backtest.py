from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.backtest import BacktestResult
from app.models.strategy import (
    BacktestEquity,
    BacktestPosition,
    BacktestRun,
    BacktestTradeRecord,
    TrainingArtifact,
)


class BacktestRepository:
    def training_diagnostics_header(self, db: Session, *, run_id: int) -> dict | None:
        """Bound artifact JSON materialization before serving training diagnostics."""
        row = db.execute(
            select(
                BacktestRun.id.label("run_id"),
                BacktestRun.training_artifact_id,
                func.length(cast(TrainingArtifact.training_input_snapshot, String)).label(
                    "artifact_snapshot_chars"
                ),
            )
            .outerjoin(
                TrainingArtifact,
                TrainingArtifact.id == BacktestRun.training_artifact_id,
            )
            .where(BacktestRun.id == run_id)
        ).mappings().first()
        return dict(row) if row else None

    def signal_audit_header(self, db: Session, *, run_id: int) -> dict | None:
        """Inspect JSON size before materializing a potentially large signal contract."""
        row = db.execute(
            select(
                BacktestRun.id,
                func.length(cast(BacktestRun.parameters, String)).label("parameter_chars"),
            ).where(BacktestRun.id == run_id)
        ).mappings().first()
        return dict(row) if row else None

    def list_equity_dates_by_run_ids(
        self, db: Session, *, run_ids: list[int]
    ) -> dict[int, tuple[date, ...]]:
        """Return each run's observed dates for comparison-window checks."""
        if not run_ids:
            return {}
        rows = db.execute(
            select(BacktestEquity.run_id, BacktestEquity.trade_date)
            .where(BacktestEquity.run_id.in_(run_ids))
            .order_by(BacktestEquity.run_id, BacktestEquity.trade_date)
        )
        values: dict[int, list[date]] = {}
        for run_id, trade_date in rows:
            values.setdefault(run_id, []).append(trade_date)
        return {run_id: tuple(dates) for run_id, dates in values.items()}

    def list_equity_values_by_run_ids(
        self, db: Session, *, run_ids: list[int]
    ) -> dict[int, list[Decimal]]:
        if not run_ids:
            return {}
        rows = db.execute(
            select(BacktestEquity.run_id, BacktestEquity.total_equity)
            .where(BacktestEquity.run_id.in_(run_ids))
            .order_by(BacktestEquity.run_id, BacktestEquity.trade_date)
        )
        values: dict[int, list[Decimal]] = {}
        for run_id, total_equity in rows:
            values.setdefault(run_id, []).append(total_equity)
        return values

    def get_run(self, db: Session, *, run_id: int) -> BacktestRun | None:
        return db.get(BacktestRun, run_id)

    def list_runs(
        self,
        db: Session,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        validation_mode: str | None = None,
        data_version: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[BacktestRun], int]:
        filters = []
        if strategy_name:
            filters.append(BacktestRun.strategy_name == strategy_name)
        if status:
            filters.append(BacktestRun.status == status)
        if validation_mode:
            filters.append(
                func.json_extract_string(
                    BacktestRun.parameters, "$.validation_mode"
                ) == validation_mode
            )
        if data_version:
            filters.append(BacktestRun.data_version == data_version)
        if start_date:
            filters.append(BacktestRun.start_date >= start_date)
        if end_date:
            filters.append(BacktestRun.end_date <= end_date)

        total = db.scalar(select(func.count()).select_from(BacktestRun).where(*filters)) or 0
        items = list(
            db.scalars(
                select(BacktestRun)
                .where(*filters)
                .order_by(BacktestRun.started_at.desc(), BacktestRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_trades(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[BacktestTradeRecord], int]:
        filters = [BacktestTradeRecord.run_id == run_id]
        if symbol:
            filters.append(BacktestTradeRecord.symbol == symbol)
        if start_date:
            filters.append(BacktestTradeRecord.trade_date >= start_date)
        if end_date:
            filters.append(BacktestTradeRecord.trade_date <= end_date)

        total = (
            db.scalar(select(func.count()).select_from(BacktestTradeRecord).where(*filters))
            or 0
        )
        items = list(
            db.scalars(
                select(BacktestTradeRecord)
                .where(*filters)
                .order_by(BacktestTradeRecord.trade_date, BacktestTradeRecord.id)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_equity(
        self,
        db: Session,
        *,
        run_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 5000,
        offset: int = 0,
    ) -> tuple[list[BacktestEquity], int]:
        filters = [BacktestEquity.run_id == run_id]
        if start_date:
            filters.append(BacktestEquity.trade_date >= start_date)
        if end_date:
            filters.append(BacktestEquity.trade_date <= end_date)

        total = db.scalar(select(func.count()).select_from(BacktestEquity).where(*filters)) or 0
        items = list(
            db.scalars(
                select(BacktestEquity)
                .where(*filters)
                .order_by(BacktestEquity.trade_date)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def list_positions(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 5000,
        offset: int = 0,
    ) -> tuple[list[BacktestPosition], int]:
        filters = [BacktestPosition.run_id == run_id]
        if symbol:
            filters.append(BacktestPosition.symbol == symbol)
        if start_date:
            filters.append(BacktestPosition.trade_date >= start_date)
        if end_date:
            filters.append(BacktestPosition.trade_date <= end_date)

        total = (
            db.scalar(select(func.count()).select_from(BacktestPosition).where(*filters))
            or 0
        )
        items = list(
            db.scalars(
                select(BacktestPosition)
                .where(*filters)
                .order_by(BacktestPosition.trade_date, BacktestPosition.symbol)
                .limit(limit)
                .offset(offset)
            )
        )
        return items, total

    def create_run(
        self,
        db: Session,
        *,
        name: str,
        strategy_name: str,
        parameters: dict[str, Any],
        code_version: str,
        data_version: str,
        start_date: date,
        end_date: date,
        initial_cash: Decimal,
        training_artifact_id: int | None = None,
    ) -> BacktestRun:
        run = BacktestRun(
            name=name,
            strategy_name=strategy_name,
            parameters=parameters,
            code_version=code_version,
            data_version=data_version,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
            training_artifact_id=training_artifact_id,
            status="running",
        )
        db.add(run)
        db.flush()
        return run

    def save_result(
        self,
        db: Session,
        *,
        run: BacktestRun,
        result: BacktestResult,
    ) -> None:
        db.add_all(
            [BacktestTradeRecord(run_id=run.id, **trade.__dict__) for trade in result.trades]
        )
        db.add_all(
            [
                BacktestEquity(run_id=run.id, **snapshot.__dict__)
                for snapshot in result.equity_curve
            ]
        )
        db.add_all(
            [
                BacktestPosition(run_id=run.id, **snapshot.__dict__)
                for snapshot in result.positions
            ]
        )
        run.final_equity = result.final_equity
        run.total_return = result.total_return
        run.status = "success"
        run.finished_at = db.scalar(select(func.now()))
        db.add(run)

    def update_data_snapshot(
        self,
        db: Session,
        *,
        run: BacktestRun,
        data_snapshot: dict[str, Any],
    ) -> None:
        run.parameters = {**run.parameters, "data_snapshot": data_snapshot}
        db.add(run)

    def update_run_parameters(
        self,
        db: Session,
        *,
        run: BacktestRun,
        extra: dict[str, Any],
    ) -> None:
        run.parameters = {**run.parameters, **extra}
        db.add(run)

    def fail_run(self, db: Session, *, run: BacktestRun, error_message: str) -> None:
        run.status = "failed"
        run.error_message = error_message
        run.finished_at = db.scalar(select(func.now()))
        db.add(run)

    def latest_positions(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> list[BacktestPosition]:
        """Position rows of the run's final trading day, symbol-ordered."""
        latest_date = db.scalar(
            select(func.max(BacktestPosition.trade_date)).where(
                BacktestPosition.run_id == run_id
            )
        )
        if latest_date is None:
            return []
        return list(
            db.scalars(
                select(BacktestPosition)
                .where(
                    BacktestPosition.run_id == run_id,
                    BacktestPosition.trade_date == latest_date,
                )
                .order_by(BacktestPosition.symbol)
            )
        )

    def latest_equity(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> BacktestEquity | None:
        return db.scalar(
            select(BacktestEquity)
            .where(BacktestEquity.run_id == run_id)
            .order_by(BacktestEquity.trade_date.desc())
            .limit(1)
        )
