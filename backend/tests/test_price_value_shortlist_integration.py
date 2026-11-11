from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.fundamental import FinancialStatement
from app.models.market import DailyBar
from app.services.price_value_screen import PriceValueScreenService
from tests.test_price_value_screen import NOW, TARGET, _add_research_history, _database


def test_shortlist_binds_continuous_profit_liquidity_and_current_version(tmp_path):
    engine = _database(tmp_path)
    service = PriceValueScreenService(output_dir=tmp_path / "screens")
    with Session(engine) as db:
        days = _add_research_history(db)
        for year in (2025, 2026):
            db.add(FinancialStatement(
                symbol="600001.SH", statement_type="income", period="half_year",
                fiscal_year=year, fiscal_period="H1", report_date=TARGET.replace(
                    year=year, month=8, day=1),
                period_end=TARGET.replace(year=year, month=6, day=30),
                currency="CNY", source_name="test", metrics={"net_profit": "80"},
                created_at=NOW - timedelta(days=21), updated_at=NOW - timedelta(days=21),
            ))
        for bar in db.scalars(select(DailyBar).where(DailyBar.symbol == "600001.SH")):
            price = Decimal("9.6") + Decimal(days.index(bar.trade_date)) * Decimal("0.02")
            bar.open = bar.high = bar.low = bar.close = price
            bar.amount = Decimal("50000000")
        db.commit()
        report = service.run(db, now=NOW)
        assert report["research_candidate_count"] == 1
        assert report["selection"]["ordered_symbols"] == ["600001.SH"]
        assert report["selection"]["qualified_count"] == 1
        assert Decimal(report["rows"][0]["selection_inputs"]["amount_mean_20"]) == 50000000
        assert service.latest(db, now=NOW)["candidate_eligible"]
        assert report["rows"][0]["shortlist"]["metrics"]["net_income_ttm"] == "100"
        assert not service._read_view(db, {
            **report, "screen_version": "relative_value_research_v6",
            "selection_version": "value_research_shortlist_v1",
        }, now=NOW)["candidate_eligible"]
        saved = next((tmp_path / "screens").glob("*.json")).read_bytes()
        assert not service._read_view(db, {**report, "selection_version": None}, now=NOW)[
            "candidate_eligible"
        ]
        assert not service._read_view(db, {**report, "selection": None}, now=NOW)[
            "candidate_eligible"
        ]
        assert not service._read_view(db, report, now=NOW + timedelta(days=1))[
            "candidate_eligible"
        ]
        assert next((tmp_path / "screens").glob("*.json")).read_bytes() == saved
        latest_income = db.scalar(select(FinancialStatement).where(
            FinancialStatement.symbol == "600001.SH",
            FinancialStatement.statement_type == "income",
            FinancialStatement.period_end == TARGET.replace(month=6, day=30),
        ))
        latest_income.metrics = {"net_profit": "-50"}
        latest_income.updated_at = NOW - timedelta(days=1)
        db.commit()
        losing = service.scan(db, now=NOW)
        assert losing["research_candidate_count"] == 1
        assert losing["selection"]["shortlist_count"] == 0
        assert "shortlist_net_income_ttm_nonpositive" in (
            losing["rows"][0]["shortlist"]["reason_codes"]
        )
        assert losing["snapshot_hash"] != report["snapshot_hash"]
        latest_income.metrics = {}
        latest_income.updated_at = NOW - timedelta(days=1)
        db.commit()
        missing = service.scan(db, now=NOW)
        assert missing["research_candidate_count"] == 1
        assert missing["rows"][0]["shortlist"]["status"] == "needs_review"
        assert "shortlist_net_income_ttm_missing_or_invalid" in (
            missing["rows"][0]["shortlist"]["reason_codes"]
        )
        latest_income.metrics = {"net_profit": "80"}
        latest_income.updated_at = NOW - timedelta(days=1)
        # A losing year must fail the new gate even if aggregate profit is positive.
        income = db.scalar(select(FinancialStatement).where(
            FinancialStatement.symbol == "600001.SH",
            FinancialStatement.statement_type == "income",
            FinancialStatement.period_end == TARGET.replace(year=2023, month=12, day=31),
        ))
        income.metrics = {"net_profit": "-10"}
        income.updated_at = NOW - timedelta(days=1)
        db.commit()
        changed = service.scan(db, now=NOW)
        assert changed["research_candidate_count"] == 1
        assert changed["selection"]["shortlist_count"] == 0
        assert changed["snapshot_hash"] != report["snapshot_hash"]
    engine.dispose()
