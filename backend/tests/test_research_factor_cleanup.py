from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.factor import FactorRun, FactorValue
from app.models.system import JobLog
from scripts.repair.isolate_price_value_roe_factor import cleanup_research_factor


def test_legacy_research_roe_factor_is_archived_and_invalidated(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'cleanup.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        run = FactorRun(
            definition_id=1, factor_name="roe_ttm", factor_version="v1",
            parameters={}, code_version="test", status="success", rows_written=2,
        )
        db.add(run)
        db.flush()
        db.add(JobLog(
            job_name="price_value_input_prep", job_type="import", status="success",
            metadata_={
                "factor_run_id": run.id,
                "target_trade_date": "2026-09-23",
            },
        ))
        for symbol in ("600001.SH", "600002.SH"):
            db.add(FactorValue(
                symbol=symbol, trade_date=date(2026, 9, 23),
                factor_name="roe_ttm", factor_version="v1",
                value=Decimal("0.12"), run_id=run.id,
            ))
        db.commit()
        result = cleanup_research_factor(
            db, run_id=run.id, output_dir=tmp_path / "audit"
        )
        assert result["archived_rows"] == 2
        assert db.scalar(select(func.count()).select_from(FactorValue)) == 0
        assert db.get(FactorRun, run.id).status == "failed"
        assert cleanup_research_factor(
            db, run_id=run.id, output_dir=tmp_path / "audit"
        ) == result
        assert db.scalar(select(func.count()).select_from(JobLog).where(
            JobLog.job_name == "price_value_research_factor_cleanup",
        )) == 1
    engine.dispose()
