"""Tests for building the curated layer directly from live DuckDB tables.

This mirrors the ``daily_loop`` path where ``market.daily_bar`` and
``raw.corporate_action`` have just been populated in the same DuckDB file.
"""

from datetime import date
from decimal import Decimal

import duckdb
import pytest

from app.lake import CuratedLayer, DataLake


def _seed_db_tables(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS market")
    connection.execute("CREATE SCHEMA IF NOT EXISTS raw")
    connection.execute(
        """
        CREATE TABLE market.daily_bar (
            symbol VARCHAR,
            security_type VARCHAR,
            trade_date DATE,
            adjust_type VARCHAR,
            open DECIMAL(18,4),
            high DECIMAL(18,4),
            low DECIMAL(18,4),
            close DECIMAL(18,4),
            volume DECIMAL(24,4),
            amount DECIMAL(24,4),
            turnover_rate DECIMAL(12,6),
            quality_status VARCHAR,
            source_name VARCHAR,
            source_count INTEGER
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE raw.corporate_action (
            symbol VARCHAR,
            ex_date DATE,
            dividend_per_share DECIMAL(18,6),
            per_share_bonus DECIMAL(18,8),
            allotment_ratio DECIMAL(18,8),
            allotment_price DECIMAL(18,6)
        )
        """
    )
    connection.executemany(
        """
        INSERT INTO market.daily_bar VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                "000001.SZ", "stock", date(2026, 1, 5), "none",
                Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"),
                Decimal("100"), Decimal("1000"), Decimal("0.01"),
                "trusted", "hithink", 1,
            ),
            (
                "000001.SZ", "stock", date(2026, 1, 6), "none",
                Decimal("9"), Decimal("9"), Decimal("9"), Decimal("9"),
                Decimal("100"), Decimal("900"), Decimal("0.02"),
                "trusted", "hithink", 1,
            ),
            (
                "000001.SZ", "stock", date(2026, 1, 7), "none",
                Decimal("9.9"), Decimal("9.9"), Decimal("9.9"), Decimal("9.9"),
                Decimal("100"), Decimal("990"), Decimal("0.015"),
                "trusted", "hithink", 1,
            ),
            # A suspect row must be excluded from the curated base.
            (
                "000002.SZ", "stock", date(2026, 1, 7), "none",
                Decimal("5"), Decimal("5"), Decimal("5"), Decimal("5"),
                Decimal("10"), Decimal("50"), Decimal("0.005"),
                "suspect", "hithink", 1,
            ),
        ],
    )
    connection.execute(
        """
        INSERT INTO raw.corporate_action VALUES
        ('000001.SZ', '2026-01-06', 1, 0, 0, 0)
        """
    )


def _read(path_glob: str) -> list[dict]:
    connection = duckdb.connect()
    try:
        cursor = connection.execute(
            f"SELECT * FROM read_parquet('{path_glob}', hive_partitioning=true) "
            "ORDER BY symbol, trade_date"
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def test_build_prices_qfq_from_db_uses_real_turnover_rate(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    connection = duckdb.connect()
    try:
        _seed_db_tables(connection)

        result = CuratedLayer(lake).build_prices_qfq_from_db(connection)

        assert result.row_count == 3
        assert result.min_date == "2026-01-05"
        rows = _read(str(lake.root / "curated" / "prices_qfq" / "**" / "*.parquet"))
        # Dividend on ex-date 01-06 scales history back.
        assert rows[0]["close"] == pytest.approx(9.0)
        assert rows[1]["close"] == pytest.approx(9.0)
        assert rows[2]["close"] == pytest.approx(9.9)
        # turnover_rate is carried from market.daily_bar, not hardcoded NULL.
        assert float(rows[0]["turnover_rate"]) == pytest.approx(0.01)
        assert float(rows[1]["turnover_rate"]) == pytest.approx(0.02)
        assert float(rows[2]["turnover_rate"]) == pytest.approx(0.015)
        # Only trusted symbols are included.
        assert {r["symbol"] for r in rows} == {"000001.SZ"}
    finally:
        connection.close()


def test_build_prices_qfq_from_db_is_idempotent(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    connection = duckdb.connect()
    try:
        _seed_db_tables(connection)
        CuratedLayer(lake).build_prices_qfq_from_db(connection)

        # Add a new bar and rebuild; the target should be replaced, not raise.
        connection.execute(
            """
            INSERT INTO market.daily_bar VALUES
            ('000001.SZ', 'stock', '2026-01-08', 'none',
             10, 10, 10, 10, 100, 1000, 0.02, 'trusted', 'hithink', 1)
            """
        )
        second = CuratedLayer(lake).build_prices_qfq_from_db(connection)

        assert second.row_count == 4
        rows = _read(str(lake.root / "curated" / "prices_qfq" / "**" / "*.parquet"))
        assert len(rows) == 4
        assert rows[-1]["trade_date"] == date(2026, 1, 8)
    finally:
        connection.close()
