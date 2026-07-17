from datetime import date

import duckdb
import pytest

from app.lake import DataLake, LakeFactorPipeline


def _sources(daily_path, action_path) -> None:
    daily_path.mkdir(parents=True)
    action_path.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        connection.execute(
            """
            COPY (
                SELECT '000001.SZ' symbol, trade_date,
                       close AS open, close AS high, close AS low, close,
                       100::BIGINT volume, 1000::DECIMAL(24,4) amount,
                       'CNY' currency
                FROM (VALUES
                    ('2026-01-05'::DATE, 10::DECIMAL(18,4)),
                    ('2026-01-06'::DATE, 9::DECIMAL(18,4)),
                    ('2026-01-07'::DATE, 9.9::DECIMAL(18,4))
                ) bars(trade_date, close)
            ) TO ? (FORMAT PARQUET)
            """,
            [str(daily_path / "data.parquet")],
        )
        connection.execute(
            """
            COPY (
                SELECT '000001.SZ' symbol, '2026-01-06'::DATE ex_date,
                       1::DECIMAL(18,8) dividend_per_share,
                       0::DECIMAL(18,8) per_share_bonus,
                       0::DECIMAL(18,8) allotment_ratio,
                       0::DECIMAL(18,8) allotment_price,
                       'CNY' currency
            ) TO ? (FORMAT PARQUET)
            """,
            [str(action_path / "data.parquet")],
        )
    finally:
        connection.close()


def _lake(tmp_path) -> DataLake:
    lake = DataLake(tmp_path / "lake")
    daily = lake.root / "daily_bar" / "release=test"
    actions = lake.root / "corporate_action" / "release=test"
    _sources(daily, actions)
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


def test_qfq_applies_action_on_ex_date_without_changing_later_scale(tmp_path) -> None:
    rows = LakeFactorPipeline(_lake(tmp_path)).query_qfq_bars()

    assert rows[0]["close"] == pytest.approx(9.0)
    assert rows[1]["close"] == pytest.approx(9.0)
    assert rows[2]["close"] == pytest.approx(9.9)
    assert rows[0]["qfq_factor"] == pytest.approx(0.9)
    assert rows[1]["qfq_factor"] == pytest.approx(1.0)


def test_factor_release_only_contains_requested_rebalance_dates(tmp_path) -> None:
    pipeline = LakeFactorPipeline(_lake(tmp_path))

    result = pipeline.build_factors(
        release="weekly-test",
        start_date=date(2026, 1, 5),
        end_date=date(2026, 1, 7),
        rebalance_dates=[date(2026, 1, 7)],
    )

    assert result.row_count == 1
    connection = duckdb.connect()
    try:
        row = connection.execute(
            "SELECT trade_date, momentum_20 FROM read_parquet(?)", [result.path]
        ).fetchone()
    finally:
        connection.close()
    assert row == (date(2026, 1, 7), None)
