from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.backtest import BacktestResult, DailyEventBacktestEngine, TargetWeightSignal
from app.repositories.market import DailyBarRepository
from app.repositories.strategy import BacktestPerformanceRepository, BacktestRepository
from app.schemas.common import Page
from app.schemas.strategy import (
    BacktestEquityRead,
    BacktestPerformanceRead,
    BacktestPositionRead,
    BacktestRunRead,
    BacktestTradeRead,
)
from app.services.performance import BacktestPerformanceService


@dataclass(frozen=True)
class PersistedBacktestResult:
    run_id: int
    result: BacktestResult


class DailyBacktestService:
    def __init__(
        self,
        daily_bar_repository: DailyBarRepository | None = None,
        backtest_repository: BacktestRepository | None = None,
    ) -> None:
        self.daily_bar_repository = daily_bar_repository or DailyBarRepository()
        self.backtest_repository = backtest_repository or BacktestRepository()

    def run(
        self,
        db: Session,
        *,
        name: str,
        strategy_name: str,
        signals: list[TargetWeightSignal],
        start_date: date,
        end_date: date,
        adjust_type: str,
        initial_cash: Decimal,
        commission_rate: Decimal,
        slippage_bps: Decimal,
        lot_size: int,
        code_version: str,
        data_version: str,
    ) -> PersistedBacktestResult:
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date")
        if adjust_type not in {"none", "qfq", "hfq"}:
            raise ValueError("adjust_type must be one of: none, qfq, hfq")
        if not signals:
            raise ValueError("signals must not be empty")
        if any(
            signal.signal_date < start_date or signal.signal_date > end_date
            for signal in signals
        ):
            raise ValueError("signal_date must be within the backtest date range")

        normalized_signals = [
            TargetWeightSignal(
                signal_date=signal.signal_date,
                symbol=signal.symbol.upper(),
                target_weight=signal.target_weight,
            )
            for signal in signals
        ]
        symbols = sorted({signal.symbol for signal in normalized_signals})
        run = self.backtest_repository.create_run(
            db,
            name=name,
            strategy_name=strategy_name,
            parameters={
                "adjust_type": adjust_type,
                "commission_rate": str(commission_rate),
                "slippage_bps": str(slippage_bps),
                "lot_size": lot_size,
                "symbols": symbols,
                "signals": [
                    {
                        "signal_date": signal.signal_date.isoformat(),
                        "symbol": signal.symbol,
                        "target_weight": str(signal.target_weight),
                    }
                    for signal in normalized_signals
                ],
            },
            code_version=code_version,
            data_version=data_version,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_cash,
        )
        try:
            bars = self.daily_bar_repository.list_for_factor_calculation(
                db,
                start_date=start_date,
                end_date=end_date,
                adjust_type=adjust_type,
                symbols=symbols,
                quality_status="trusted",
            )
            result = DailyEventBacktestEngine(
                initial_cash=initial_cash,
                commission_rate=commission_rate,
                slippage_bps=slippage_bps,
                lot_size=lot_size,
            ).run(bars=bars, signals=normalized_signals)
            self.backtest_repository.save_result(db, run=run, result=result)
        except Exception as exc:
            self.backtest_repository.fail_run(db, run=run, error_message=str(exc))
            raise
        return PersistedBacktestResult(run_id=run.id, result=result)


class BacktestQueryService:
    def __init__(
        self,
        backtest_repository: BacktestRepository | None = None,
        performance_repository: BacktestPerformanceRepository | None = None,
        performance_service: BacktestPerformanceService | None = None,
    ) -> None:
        self.backtest_repository = backtest_repository or BacktestRepository()
        self.performance_repository = (
            performance_repository or BacktestPerformanceRepository()
        )
        self.performance_service = performance_service or BacktestPerformanceService(
            performance_repository=self.performance_repository
        )

    def list_runs(
        self,
        db: Session,
        *,
        strategy_name: str | None = None,
        status: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestRunRead]:
        rows, total = self.backtest_repository.list_runs(
            db,
            strategy_name=strategy_name,
            status=status,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestRunRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_run(self, db: Session, *, run_id: int) -> BacktestRunRead | None:
        run = self.backtest_repository.get_run(db, run_id=run_id)
        return None if run is None else BacktestRunRead.model_validate(run)

    def list_trades(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestTradeRead]:
        rows, total = self.backtest_repository.list_trades(
            db,
            run_id=run_id,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestTradeRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_equity(
        self,
        db: Session,
        *,
        run_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestEquityRead]:
        rows, total = self.backtest_repository.list_equity(
            db,
            run_id=run_id,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestEquityRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def list_positions(
        self,
        db: Session,
        *,
        run_id: int,
        symbol: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int,
        offset: int,
    ) -> Page[BacktestPositionRead]:
        rows, total = self.backtest_repository.list_positions(
            db,
            run_id=run_id,
            symbol=symbol.upper() if symbol else None,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
        return Page(
            items=[BacktestPositionRead.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    def get_performance(
        self,
        db: Session,
        *,
        run_id: int,
    ) -> BacktestPerformanceRead | None:
        row = self.performance_repository.get_by_run_id(db, run_id=run_id)
        return None if row is None else BacktestPerformanceRead.model_validate(row)

    def analyze_performance(
        self,
        db: Session,
        *,
        run_id: int,
        code_version: str,
        style_factors: dict[str, tuple[str, str]] | None = None,
    ) -> BacktestPerformanceRead:
        self.performance_service.analyze(
            db,
            run_id=run_id,
            code_version=code_version,
            style_factors=style_factors,
        )
        performance = self.performance_repository.get_by_run_id(db, run_id=run_id)
        if performance is None:
            raise ValueError("backtest performance not found after analysis")
        return BacktestPerformanceRead.model_validate(performance)
