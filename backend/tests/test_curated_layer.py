from datetime import date

import duckdb
import pytest

from app.lake import CuratedLayer, DataLake


def _seed_price_lake(tmp_path) -> DataLake:
    lake = DataLake(tmp_path / "lake")
    daily = lake.root / "daily_bar" / "release=test"
    actions = lake.root / "corporate_action" / "release=test"
    daily.mkdir(parents=True)
    actions.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            COPY (
                SELECT '000001.SZ' symbol, trade_date,
                       close AS open, close AS high, close AS low, close,
                       100::BIGINT volume, 1000::DECIMAL(24,4) amount
                FROM (VALUES
                    ('2026-01-05'::DATE, 10::DECIMAL(18,4)),
                    ('2026-01-06'::DATE, 9::DECIMAL(18,4)),
                    ('2026-01-07'::DATE, 9.9::DECIMAL(18,4))
                ) bars(trade_date, close)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(daily / "data.parquet")],
        )
        connection.execute(
            """
            COPY (
                SELECT '000001.SZ' symbol, '2026-01-06'::DATE ex_date,
                       1::DECIMAL(18,8) dividend_per_share,
                       0::DECIMAL(18,8) per_share_bonus,
                       0::DECIMAL(18,8) allotment_ratio,
                       0::DECIMAL(18,8) allotment_price
            ) TO ? (FORMAT PARQUET)
            """,
            [str(actions / "data.parquet")],
        )
    finally:
        connection.close()
    lake._write_manifest(  # noqa: SLF001
        [
            {
                "dataset": dataset,
                "release": "test",
                "path": str(path),
                "source_path": "test",
                "source_sha256": "test",
                "row_count": count,
                "min_date": "2026-01-05",
                "max_date": "2026-01-07",
                "created_at": "2026-01-07T00:00:00+00:00",
            }
            for dataset, path, count in (
                ("daily_bar", daily, 3),
                ("corporate_action", actions, 1),
            )
        ]
    )
    return lake


def _read(path_glob: str, order_by: str = "symbol, trade_date") -> list[dict]:
    connection = duckdb.connect()
    try:
        cursor = connection.execute(
            f"SELECT * FROM read_parquet('{path_glob}', hive_partitioning=true) "
            f"ORDER BY {order_by}"
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
    finally:
        connection.close()


def test_build_prices_qfq_materializes_forward_adjusted_partitions(tmp_path) -> None:
    lake = _seed_price_lake(tmp_path)

    result = CuratedLayer(lake).build_prices_qfq()

    assert result.row_count == 3
    assert result.min_date == "2026-01-05"
    rows = _read(str(lake.root / "curated" / "prices_qfq" / "**" / "*.parquet"))
    # Dividend on ex-date 01-06 scales history back; latest bar keeps raw price.
    assert rows[0]["close"] == pytest.approx(9.0)
    assert rows[1]["close"] == pytest.approx(9.0)
    assert rows[2]["close"] == pytest.approx(9.9)
    assert rows[0]["qfq_factor"] == pytest.approx(0.9)
    # Partition columns are materialized for pruning.
    assert rows[2]["year"] == 2026 and rows[2]["month"] == 1


def test_build_prices_qfq_refuses_to_clobber(tmp_path) -> None:
    lake = _seed_price_lake(tmp_path)
    CuratedLayer(lake).build_prices_qfq()

    with pytest.raises(FileExistsError):
        CuratedLayer(lake).build_prices_qfq()


def _fundamentals_source(tmp_path) -> str:
    source = tmp_path / "financials.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('600519.SH', 'income',   '2026-04-15'::DATE, '2026-03-31'::DATE,
                     2026, 'Q1', '{"operating_revenue": "500", "net_profit": "120"}'),
                    ('600519.SH', 'balance',  '2026-04-15'::DATE, '2026-03-31'::DATE,
                     2026, 'Q1', '{"total_assets": "2000", "total_equity": "1500"}'),
                    ('600519.SH', 'income',   '2025-04-20'::DATE, '2025-03-31'::DATE,
                     2025, 'Q1', '{"revenue": "400", "net_income": "90"}')
                ) t(symbol, statement_type, report_date, period_end,
                     fiscal_year, fiscal_period, metrics)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(source)],
        )
    finally:
        connection.close()
    return str(source)


def test_build_fundamentals_pit_flattens_json_into_typed_columns(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    source = _fundamentals_source(tmp_path)

    result = CuratedLayer(lake).build_fundamentals_pit(source)

    # Two disclosure periods (Q1 2026 income+balance collapse to one row; Q1 2025).
    assert result.row_count == 2
    rows = _read(
        str(lake.root / "curated" / "fundamentals_pit" / "**" / "*.parquet"),
        order_by="symbol, report_date",
    )
    q1_2026 = next(r for r in rows if r["fiscal_year"] == 2026)
    assert q1_2026["report_date"] == date(2026, 4, 15)
    assert q1_2026["operating_income"] == pytest.approx(500)
    assert q1_2026["net_profit"] == pytest.approx(120)
    assert q1_2026["total_assets"] == pytest.approx(2000)
    assert q1_2026["total_equity"] == pytest.approx(1500)
    # Alias fallback: 2025 row used vendor spellings revenue/net_income.
    q1_2025 = next(r for r in rows if r["fiscal_year"] == 2025)
    assert q1_2025["operating_income"] == pytest.approx(400)
    assert q1_2025["net_profit"] == pytest.approx(90)


def test_fundamentals_pit_supports_point_in_time_asof_join(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    source = _fundamentals_source(tmp_path)
    CuratedLayer(lake).build_fundamentals_pit(source)
    pit_glob = str(lake.root / "curated" / "fundamentals_pit" / "**" / "*.parquet")

    connection = duckdb.connect()
    try:
        # On a trade date before the 2026 disclosure, only the 2025 statement is
        # visible — the ASOF join must not leak the not-yet-reported figures.
        row = connection.execute(
            f"""
            WITH grid(symbol, trade_date) AS (VALUES ('600519.SH', '2026-04-10'::DATE))
            SELECT g.trade_date, f.net_profit
            FROM grid g
            ASOF JOIN read_parquet('{pit_glob}', hive_partitioning=true) f
              ON g.symbol = f.symbol AND f.report_date < g.trade_date
            """
        ).fetchone()
    finally:
        connection.close()
    assert row[1] == pytest.approx(90)
