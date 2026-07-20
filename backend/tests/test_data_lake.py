from datetime import date
from pathlib import Path

import duckdb

from app.lake import DataLake


def _daily_source(path) -> None:
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            COPY (
                SELECT
                    '600519.SH'::VARCHAR AS thscode,
                    1784217600000::BIGINT AS date_ms,
                    10::DECIMAL(18,4) AS open_price,
                    11::DECIMAL(18,4) AS high_price,
                    9::DECIMAL(18,4) AS low_price,
                    10.5::DECIMAL(18,4) AS close_price,
                    100::BIGINT AS volume,
                    1050::DECIMAL(24,4) AS turnover,
                    'CNY'::VARCHAR AS currency,
                    'none'::VARCHAR AS adjusted
            ) TO ? (FORMAT PARQUET)
            """,
            [str(path)],
        )
    finally:
        connection.close()


def test_data_lake_imports_partitions_and_queries(tmp_path) -> None:
    source = tmp_path / "daily.parquet"
    _daily_source(source)
    lake = DataLake(tmp_path / "lake")

    version = lake.import_daily_bars(source, release="test-release")
    rows = lake.query_daily_bars(
        symbols=["600519.SH"],
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
    )

    assert version.row_count == 1
    assert version.min_date == "2026-07-17"
    assert rows[0]["symbol"] == "600519.SH"
    assert lake.latest("daily_bar").release == "test-release"


def test_data_lake_appends_json_payloads(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")

    target = lake.append_payloads(
        "hot_stock",
        [{"thscode": "600519.SH", "rank": 1}],
        partition_date=date(2026, 7, 17),
    )

    assert target.exists()
    connection = duckdb.connect()
    try:
        payload = connection.execute(
            "SELECT payload->>'thscode' FROM read_parquet(?)",
            [str(target)],
        ).fetchone()[0]
    finally:
        connection.close()
    assert payload == "600519.SH"


def test_data_lake_prunes_old_generated_versions(tmp_path) -> None:
    source = tmp_path / "daily.parquet"
    _daily_source(source)
    lake = DataLake(tmp_path / "lake")
    first = lake.import_daily_bars(source, release="first")
    second = lake.import_daily_bars(source, release="second")

    removed = lake.prune_old_versions("daily_bar", keep=1)

    assert removed == ["first"]
    assert not Path(first.path).exists()
    assert Path(second.path).exists()
    assert lake.latest("daily_bar").release == "second"
