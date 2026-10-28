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
                    adjust_type VARCHAR,
                    quality_status VARCHAR,
                    is_suspect BOOLEAN
                )
            """)
        )
        connection.execute(
            text("""
                CREATE TABLE market.security (
                    symbol VARCHAR,
                    exchange VARCHAR,
                    security_type VARCHAR,
                    is_active BOOLEAN,
                    list_date DATE,
                    delist_date DATE
                )
            """)
        )
        connection.execute(
            text("""
                CREATE TABLE factor.factor_value (
                    symbol VARCHAR,
                    trade_date DATE,
                    factor_name VARCHAR,
                    factor_version VARCHAR
                )
            """)
        )
        connection.execute(
            text("""
                CREATE TABLE market.adjustment_factor (
                    id BIGINT,
                    symbol VARCHAR,
                    trade_date DATE,
                    adjust_type VARCHAR,
                    factor DOUBLE,
                    source_name VARCHAR,
                    version VARCHAR,
                    release_id VARCHAR,
                    quality_status VARCHAR,
                    updated_at TIMESTAMP
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
                    (1, '000001.SZ', :trade_date, 'none', 'trusted', false)
            """),
            {"trade_date": target_date},
        )
        db.execute(
            text("""
                INSERT INTO market.security VALUES
                    ('000001.SZ', 'SZ', 'stock', true, '1991-04-03', NULL)
            """)
        )
        db.execute(
            text("""
                INSERT INTO market.adjustment_factor VALUES
                    (NULL, '000001.SZ', :trade_date, 'qfq', 1.0,
                     'hithink_finance_dump', 'v1', NULL, 'trusted', now())
            """),
            {"trade_date": target_date},
        )
        for factor_name in (
            "momentum_20",
            "momentum_60",
            "volatility_20",
            "downside_volatility_20",
            "max_drawdown_20",
            "liquidity_20",
            "volume_stability_20",
        ):
            db.execute(
                text("""
                    INSERT INTO factor.factor_value VALUES
                        ('000001.SZ', :trade_date, :factor_name, 'v1-qfq')
                """),
                {"trade_date": target_date, "factor_name": factor_name},
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
    assert metadata["gate_core_factor_partial"] is False
    assert metadata["daily_factor_coverage"]["momentum_20"]["rows"] == 1
    assert "beta_60" in metadata["daily_factor_incomplete"]
    engine.dispose()


def test_certification_gate_blocks_missing_qfq_rows() -> None:
    factory, engine = _session_factory()
    target_date = date(2026, 9, 17)
    with factory() as db:
        db.execute(
            text("""
                INSERT INTO market.daily_bar VALUES
                    (1, '000001.SZ', :trade_date, 'none', 'trusted', false)
            """),
            {"trade_date": target_date},
        )
        db.execute(
            text("""
                INSERT INTO market.security VALUES
                    ('000001.SZ', 'SZ', 'stock', true, '1991-04-03', NULL)
            """),
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
