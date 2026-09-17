from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.scheduler.daily_loop import _gather_certification_metadata


def _session_factory():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE SCHEMA market"))
        connection.execute(text("CREATE SCHEMA factor"))
        connection.execute(
            text("""
                CREATE TABLE market.daily_bar (
                    id BIGINT,
                    symbol VARCHAR,
                    trade_date DATE,
                    adjust_type VARCHAR
                )
            """)
        )
        connection.execute(
            text("""
                CREATE TABLE factor.factor_run (
                    status VARCHAR,
                    input_end_date DATE
                )
            """)
        )
    return sessionmaker(bind=engine), engine


def test_certification_gate_accepts_complete_qfq_coverage() -> None:
    factory, engine = _session_factory()
    target_date = date(2026, 9, 17)
    with factory() as db:
        db.execute(
            text("""
                INSERT INTO market.daily_bar VALUES
                    (1, '000001.SZ', :trade_date, 'none'),
                    (2, '000001.SZ', :trade_date, 'qfq')
            """),
            {"trade_date": target_date},
        )
        db.execute(
            text("INSERT INTO factor.factor_run VALUES ('success', :trade_date)"),
            {"trade_date": target_date},
        )
        db.commit()

        metadata = _gather_certification_metadata(
            db,
            target_date,
            code_version="test",
        )

    assert metadata["gate_qfq_coverage_anomaly"] is False
    assert metadata["qfq_missing_rows"] == 0
    assert "QFQ_COVERAGE_GAP" not in metadata["reason_codes"]
    engine.dispose()


def test_certification_gate_blocks_missing_qfq_rows() -> None:
    factory, engine = _session_factory()
    target_date = date(2026, 9, 17)
    with factory() as db:
        db.execute(
            text("INSERT INTO market.daily_bar VALUES (1, '000001.SZ', :trade_date, 'none')"),
            {"trade_date": target_date},
        )
        db.execute(
            text("INSERT INTO factor.factor_run VALUES ('success', :trade_date)"),
            {"trade_date": target_date},
        )
        db.commit()

        metadata = _gather_certification_metadata(
            db,
            target_date,
            code_version="test",
        )

    assert metadata["gate_qfq_coverage_anomaly"] is True
    assert metadata["qfq_missing_rows"] == 1
    assert "QFQ_COVERAGE_GAP" in metadata["reason_codes"]
    engine.dispose()
