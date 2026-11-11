"""Repository reads must preserve decimal tokens stored in financial JSON."""

from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.fundamental import FinancialStatement
from app.repositories.strategy.price_value_screen import PriceValueScreenRepository
from app.services.quality_value_financial import financial_metrics


def test_diagnostic_financial_json_keeps_exact_numeric_tokens(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'financial-read.duckdb'}")
    try:
        initialize_database(engine)
        with Session(engine) as db:
            # Bind raw JSON text to model the vendor's decimal JSON payload.
            # Constructing a Python float first would already lose precision.
            for year in (2023, 2024, 2025):
                db.execute(text("""
                    INSERT INTO fundamental.financial_statement
                    (symbol, statement_type, period, fiscal_year, fiscal_period,
                     period_end, report_date, currency, metrics, source_name,
                     created_at, updated_at)
                    VALUES ('600001.SH', 'income', 'annual', :year, 'annual',
                            :period_end, :report_date, 'CNY', CAST(:metrics AS JSON),
                            'fixture', '2026-10-01T00:00:00+00:00',
                            '2026-10-01T00:00:00+00:00')
                """), {
                    "year": year, "period_end": date(year, 12, 31),
                    "report_date": date(year + 1, 4, 1),
                    "metrics": '{"net_profit":123456789.123456789}',
                })
            db.commit()
            # This is the lossy path being repaired, not an accepted input.
            orm_metrics = db.scalar(select(FinancialStatement.metrics).limit(1))
            assert isinstance(orm_metrics["net_profit"], float)
            rows = PriceValueScreenRepository().diagnostic_inputs(
                db, trade_date=date(2026, 10, 28),
            )["financial_rows"]
            assert len(rows) == 3
            assert all(isinstance(row["metrics"], str) for row in rows)
            assert all("123456789.123456789" in row["metrics"] for row in rows)
            metrics = financial_metrics(rows, date(2026, 10, 28))
            assert metrics["net_income_sum_3y"].value == Decimal("370370367.370370367")
            # Replacing the exact payload with an ORM-decoded float still fails.
            lossy_rows = [{**row, "metrics": orm_metrics} for row in rows]
            assert "net_income_sum_3y" not in financial_metrics(
                lossy_rows, date(2026, 10, 28),
            )
    finally:
        engine.dispose()
