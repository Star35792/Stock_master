from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backtest import BacktestResult
from app.models.strategy import (
    BacktestEquity,
    BacktestPosition,
    BacktestRun,
    BacktestTradeRecord,
)


class BacktestRepository:
    def get_run(self, db: Session, *, run_id: int) -> BacktestRun | None:
        return db.get(BacktestRun, run_id)

    def list_runs(
        self,
        db: Session,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
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

    def fail_run(self, db: Session, *, run: BacktestRun, error_message: str) -> None:
        run.status = "failed"
        run.error_message = error_message
        run.finished_at = db.scalar(select(func.now()))
        db.add(run)
