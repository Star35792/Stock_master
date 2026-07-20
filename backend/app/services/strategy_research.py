from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.repositories.factor import FactorValueRepository
from app.repositories.market import SecurityRepository
from app.services.backtest import DailyBacktestService, PersistedBacktestResult
from app.strategies import FactorScoreSpec, MultiFactorStrategy


class StrategyResearchService:
    def __init__(
        self,
        factor_value_repository: FactorValueRepository | None = None,
        security_repository: SecurityRepository | None = None,
        backtest_service: DailyBacktestService | None = None,
    ) -> None:
        self.factor_value_repository = factor_value_repository or FactorValueRepository()
        self.security_repository = security_repository or SecurityRepository()
        self.backtest_service = backtest_service or DailyBacktestService()

    def run_factor_selection(
        self,
        db: Session,
        *,
        name: str,
        factor_specs: list[FactorScoreSpec],
        top_n: int,
        allowed_security_types: set[str],
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
        if not factor_specs:
            raise ValueError("factor_specs must not be empty")
        factor_rows = {
            spec: self.factor_value_repository.list_for_cross_section(
                db,
                factor_name=spec.factor_name,
                factor_version=spec.factor_version,
                start_date=start_date,
                end_date=end_date,
            )
            for spec in factor_specs
        }
        symbols = {
            row.symbol
            for rows in factor_rows.values()
            for row in rows
        }
        security_types = self.security_repository.get_security_types(db, symbols)
        signals = MultiFactorStrategy(
            top_n=top_n,
            allowed_security_types=allowed_security_types,
        ).generate(
            factor_rows=factor_rows,
            security_types=security_types,
        )
        return self.backtest_service.run(
            db,
            name=name,
            strategy_name="factor_selection",
            signals=signals,
            start_date=start_date,
            end_date=end_date,
            adjust_type=adjust_type,
            initial_cash=initial_cash,
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
            lot_size=lot_size,
            code_version=code_version,
            data_version=data_version,
        )
