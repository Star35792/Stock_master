from sqlalchemy import select
from sqlalchemy.orm import Session

from app.backtest import PerformanceReport
from app.models.strategy import (
    BacktestEquity,
    BacktestPerformance,
    BacktestPosition,
    BacktestRun,
    BacktestTradeRecord,
)


class BacktestPerformanceRepository:
    def get_by_run_id(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> BacktestPerformance | None:
        return db.scalar(
            select(BacktestPerformance).where(BacktestPerformance.run_id == run_id)
        )

    def load_inputs(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> tuple[
        BacktestRun | None,
        list[BacktestEquity],
        list[BacktestTradeRecord],
        list[BacktestPosition],
    ]:
        run = db.get(BacktestRun, run_id)
        equity_rows = list(
            db.scalars(
                select(BacktestEquity)
                .where(BacktestEquity.run_id == run_id)
                .order_by(BacktestEquity.trade_date)
            )
        )
        trade_rows = list(
            db.scalars(
                select(BacktestTradeRecord)
                .where(BacktestTradeRecord.run_id == run_id)
                .order_by(BacktestTradeRecord.trade_date, BacktestTradeRecord.id)
            )
        )
        position_rows = list(
            db.scalars(
                select(BacktestPosition)
                .where(BacktestPosition.run_id == run_id)
                .order_by(BacktestPosition.trade_date, BacktestPosition.symbol)
            )
        )
        return run, equity_rows, trade_rows, position_rows

    def save(
        self,
        db: Session,
        *,
        run_id: int,
        report: PerformanceReport,
        code_version: str,
    ) -> BacktestPerformance:
        row = db.scalar(
            select(BacktestPerformance).where(BacktestPerformance.run_id == run_id)
        )
        values = {
            "total_return": report.total_return,
            "annualized_return": report.annualized_return,
            "maximum_drawdown": report.maximum_drawdown,
            "annualized_volatility": report.annualized_volatility,
            "sharpe_ratio": report.sharpe_ratio,
            "calmar_ratio": report.calmar_ratio,
            "daily_win_rate": report.daily_win_rate,
            "profit_loss_ratio": report.profit_loss_ratio,
            "turnover_rate": report.turnover_rate,
            "total_commission": report.total_commission,
            "symbol_contributions": self._serialize(report.symbol_contributions),
            "industry_exposure": self._serialize(report.industry_exposure),
            "style_exposure": self._serialize(report.style_exposure),
            "code_version": code_version,
        }
        if row is None:
            row = BacktestPerformance(run_id=run_id, **values)
        else:
            for field_name, value in values.items():
                setattr(row, field_name, value)
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def _serialize(values: dict[str, object]) -> dict[str, str]:
        return {key: str(value) for key, value in values.items()}
