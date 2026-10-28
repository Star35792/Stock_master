from datetime import date

import duckdb
import pytest

from app.repositories.strategy.quality_value_data import QualityValueDataRepository


@pytest.mark.parametrize("stored", ["ST", "*ST", "S*ST", "SST", "star_st"])
def test_quality_value_bars_normalize_restricted_treatment_aliases(stored):
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA market")
    connection.execute("""
        CREATE TABLE market.daily_bar (
            symbol VARCHAR, trade_date DATE, adjust_type VARCHAR,
            security_type VARCHAR, close DECIMAL(18, 4), source_name VARCHAR,
            special_treatment_status VARCHAR, is_suspended BOOLEAN,
            quality_status VARCHAR, is_suspect BOOLEAN, updated_at TIMESTAMPTZ
        )
    """)
    connection.execute("""
        INSERT INTO market.daily_bar VALUES
        ('000001.SZ', '2026-10-27', 'none', 'stock', 10, 'fixture', ?,
         false, 'trusted', false, '2026-10-27 12:00:00+00')
    """, [stored])

    rows = QualityValueDataRepository(connection).bars(trade_date=date(2026, 10, 27))

    assert rows[0]["special_treatment_status"] == "st"
    connection.close()


def test_quality_value_bars_preserve_normal_and_unknown_treatment_meaning():
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA market")
    connection.execute("""
        CREATE TABLE market.daily_bar (
            symbol VARCHAR, trade_date DATE, adjust_type VARCHAR,
            security_type VARCHAR, close DECIMAL(18, 4), source_name VARCHAR,
            special_treatment_status VARCHAR, is_suspended BOOLEAN,
            quality_status VARCHAR, is_suspect BOOLEAN, updated_at TIMESTAMPTZ
        )
    """)
    connection.execute("""
        INSERT INTO market.daily_bar VALUES
        ('000001.SZ', '2026-10-27', 'none', 'stock', 10, 'fixture', ' Normal ',
         false, 'trusted', false, '2026-10-27 12:00:00+00'),
        ('000002.SZ', '2026-10-27', 'none', 'stock', 10, 'fixture', NULL,
         false, 'trusted', false, '2026-10-27 12:00:00+00')
    """)

    rows = QualityValueDataRepository(connection).bars(trade_date=date(2026, 10, 27))

    statuses = {row["symbol"]: row["special_treatment_status"] for row in rows}
    assert statuses == {"000001.SZ": "normal", "000002.SZ": None}
    connection.close()
