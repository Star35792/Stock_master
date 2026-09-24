from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.factor import FactorValue
from app.models.market import DailyBar, ValuationSnapshot
from app.repositories.market import ValuationSnapshotRepository
from app.services.valuation_factors import ValuationFactorService


def test_research_valuation_never_enters_generic_factor_or_market_cap(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'research-isolation.duckdb'}")
    initialize_database(engine)
    target = date(2026, 9, 23)
    with Session(engine) as db:
        db.add(DailyBar(
            symbol="600001.SH", security_type="stock", trade_date=target,
            adjust_type="none", open=Decimal("10"), high=Decimal("10"),
            low=Decimal("10"), close=Decimal("10"), volume=Decimal("100"),
            amount=Decimal("1000"), quality_status="trusted",
            source_name="test", is_suspect=False,
        ))
        db.add_all([
            ValuationSnapshot(
                symbol="600001.SH", trade_date=target,
                total_market_cap=Decimal("1000"), currency="CNY",
                source_name="trusted_fixture", raw_payload={},
            ),
            ValuationSnapshot(
                symbol="600001.SH", trade_date=target,
                total_market_cap=Decimal("9999"), currency="CNY",
                source_name="eastmoney", raw_payload={"_research_batch_id": 123},
            ),
        ])
        db.commit()
        repository = ValuationSnapshotRepository()
        rows = repository.list_available(db, start_date=target, end_date=target)
        assert [row.source_name for row in rows] == ["trusted_fixture"]
        assert repository.latest_market_cap_by_symbol(
            db, trade_date=target
        ) == {"600001.SH": Decimal("1000")}
        result = ValuationFactorService().run(
            db, start_date=target, end_date=target,
            factor_names=["market_cap"], adjust_type="none",
        )
        assert result.valuation_rows == 1
        assert db.scalar(select(FactorValue.value).where(
            FactorValue.symbol == "600001.SH",
            FactorValue.trade_date == target,
            FactorValue.factor_name == "market_cap",
        )) == Decimal("1000")
    engine.dispose()
