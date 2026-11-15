from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.lake import DataLake
from app.lake.data_lake import _compact_partitions


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


def test_partition_compaction_publishes_verified_file_before_reclaim(tmp_path) -> None:
    root = tmp_path / "lake"
    partition = root / "trade_year=2026" / "trade_month=7"
    partition.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        for value in (1, 2):
            target_sql = (partition / f"part-{value}.parquet").as_posix().replace("'", "''")
            connection.execute(
                f"COPY (SELECT ?::INTEGER AS id) TO '{target_sql}' (FORMAT PARQUET)",
                [value],
            )
    finally:
        connection.close()

    _compact_partitions(root)

    assert [path.name for path in partition.iterdir()] == ["data.parquet"]
    connection = duckdb.connect()
    try:
        assert connection.execute(
            "SELECT id FROM read_parquet(?) ORDER BY id",
            [str(partition / "data.parquet")],
        ).fetchall() == [(1,), (2,)]
    finally:
        connection.close()
    assert not list(root.rglob("*.replacing-*"))


def test_partition_compaction_restores_originals_when_publish_fails(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "lake"
    partition = root / "trade_year=2026" / "trade_month=7"
    partition.mkdir(parents=True)
    connection = duckdb.connect()
    try:
        for value in (1, 2):
            target_sql = (partition / f"part-{value}.parquet").as_posix().replace("'", "''")
            connection.execute(
                f"COPY (SELECT {value} AS id) TO '{target_sql}' (FORMAT PARQUET)"
            )
    finally:
        connection.close()

    original_rename = Path.rename

    def fail_staged_publish(self: Path, target: Path) -> Path:
        if ".compact-" in self.name:
            raise OSError("simulated publish failure")
        return original_rename(self, target)

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "rename", fail_staged_publish)
        with pytest.raises(OSError, match="simulated publish failure"):
            _compact_partitions(root)

    assert sorted(path.name for path in partition.glob("*.parquet")) == [
        "part-1.parquet", "part-2.parquet"
    ]
    assert not list(root.rglob("*.compact-*"))
    assert not list(root.rglob("*.replacing-*"))
