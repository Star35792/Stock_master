from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.backtest import BacktestResult, DailyEventBacktestEngine, TargetWeightSignal
from app.repositories.market import DailyBarRepository
from app.repositories.strategy import BacktestRepository


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
