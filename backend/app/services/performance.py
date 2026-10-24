from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.backtest import BacktestPerformanceAnalyzer, PerformanceReport
from app.repositories.factor import FactorValueRepository
from app.repositories.market import SecurityRepository
from app.repositories.strategy import BacktestPerformanceRepository


@dataclass(frozen=True)
class PersistedPerformanceReport:
    performance_id: int
    run_id: int
    report: PerformanceReport


class BacktestPerformanceService:
    def __init__(
        self,
        performance_repository: BacktestPerformanceRepository | None = None,
        security_repository: SecurityRepository | None = None,
        factor_value_repository: FactorValueRepository | None = None,
        analyzer: BacktestPerformanceAnalyzer | None = None,
    ) -> None:
        self.performance_repository = (
            performance_repository or BacktestPerformanceRepository()
        )
        self.security_repository = security_repository or SecurityRepository()
        self.factor_value_repository = factor_value_repository or FactorValueRepository()
        self.analyzer = analyzer or BacktestPerformanceAnalyzer()

    def analyze(
        self,
        db: Session,
        *,
        run_id: int,
        code_version: str,
        style_factors: dict[str, tuple[str, str]] | None = None,
        research_db: Session | None = None,
    ) -> PersistedPerformanceReport:
        # Strategy inputs persist in the research database; industry and
        # factor reads stay on the main database.
        artifacts_db = research_db if research_db is not None else db
        run, equity_rows, trade_rows, position_rows = (
            self.performance_repository.load_inputs(artifacts_db, run_id=run_id)
        )
        if run is None:
            raise ValueError("backtest run not found")
        if run.status != "success":
            raise ValueError("backtest run must be successful")

        symbols = {row.symbol for row in position_rows} | {
            row.symbol for row in trade_rows
        }
        industries = self.security_repository.get_industries(db, symbols)
        style_values = {}
        for style_name, (factor_name, factor_version) in (style_factors or {}).items():
            rows = self.factor_value_repository.list_for_cross_section(
                db,
                factor_name=factor_name,
                factor_version=factor_version,
                start_date=run.end_date,
                end_date=run.end_date,
            )
            style_values[style_name] = {row.symbol: row.value for row in rows}

        report = self.analyzer.analyze(
            run=run,
            equity_rows=equity_rows,
            trade_rows=trade_rows,
            position_rows=position_rows,
            industries=industries,
            style_values=style_values,
        )
        performance = self.performance_repository.save(
            artifacts_db,
            run_id=run_id,
            report=report,
            code_version=code_version,
        )
        return PersistedPerformanceReport(
            performance_id=performance.id,
            run_id=run_id,
            report=report,
        )
