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
        run.finished_at = db.scalar(select(func.clock_timestamp()))
        db.add(run)

    def fail_run(self, db: Session, *, run: BacktestRun, error_message: str) -> None:
        run.status = "failed"
        run.error_message = error_message
        run.finished_at = db.scalar(select(func.clock_timestamp()))
        db.add(run)
