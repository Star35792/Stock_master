"""Tests for building the curated layer directly from live DuckDB tables.

This mirrors the ``daily_loop`` path where ``AdjustmentBuildService`` has
populated ``market.adjustment_factor`` with ``adjust_type='qfq'`` factors and
``market.daily_bar`` holds only ``adjust_type='none'`` rows.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from app.lake import CuratedDatasetResult, CuratedLayer, DataLake


def _seed_db_tables(db_path: Path) -> None:
    connection = duckdb.connect(str(db_path))
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS market")
        connection.execute(
            """
            CREATE TABLE market.adjustment_factor (
                id BIGINT,
                symbol VARCHAR,
                trade_date DATE,
                adjust_type VARCHAR,
                factor DECIMAL(24,10),
                source_name VARCHAR,
                version VARCHAR,
                updated_at TIMESTAMPTZ
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO market.adjustment_factor
            VALUES (?, ?, ?, 'qfq', ?, 'hithink_finance_dump', 'v1', now())
            """,
            [
                (1, "000001.SZ", date(2026, 1, 5), Decimal("0.9")),
                (2, "000001.SZ", date(2026, 1, 6), Decimal("0.9")),
                (3, "000001.SZ", date(2026, 1, 7), Decimal("1.0")),
            ],
        )
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
        connection.executemany(
            """
            INSERT INTO market.daily_bar VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            [
                # Unadjusted rows; the curated layer multiplies by the QFQ factor.
                (
                    "000001.SZ",
                    "stock",
                    date(2026, 1, 5),
                    "none",
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("100"),
                    Decimal("900"),
                    Decimal("0.01"),
                    "trusted",
                    "hithink",
                    1,
                ),
                (
                    "000001.SZ",
                    "stock",
                    date(2026, 1, 6),
                    "none",
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("10"),
                    Decimal("100"),
                    Decimal("900"),
                    Decimal("0.02"),
                    "trusted",
                    "hithink",
                    1,
                ),
                (
                    "000001.SZ",
                    "stock",
                    date(2026, 1, 7),
                    "none",
                    Decimal("9.9"),
                    Decimal("9.9"),
                    Decimal("9.9"),
                    Decimal("9.9"),
                    Decimal("100"),
                    Decimal("990"),
                    Decimal("0.015"),
                    "trusted",
                    "hithink",
                    1,
                ),
                # A non-trusted row must be excluded from the curated layer.
                (
                    "000002.SZ",
                    "stock",
                    date(2026, 1, 7),
                    "none",
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("10"),
                    Decimal("50"),
                    Decimal("0.005"),
                    "suspect",
                    "hithink",
                    1,
                ),
                # A row with no matching factor must be excluded (cannot compute qfq).
                (
                    "000003.SZ",
                    "stock",
                    date(2026, 1, 7),
                    "none",
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("5"),
                    Decimal("10"),
                    Decimal("50"),
                    Decimal("0.005"),
                    "trusted",
                    "hithink",
                    1,
                ),
            ],
        )
    finally:
        connection.close()


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
    db_path = tmp_path / "stock_master.duckdb"
    _seed_db_tables(db_path)

    result = CuratedLayer(lake).build_prices_qfq_from_db(str(db_path))

    assert result.row_count == 3
    assert result.min_date == "2026-01-05"
    rows = _read(str(lake.root / "curated" / "prices_qfq" / "**" / "*.parquet"))
    assert len(rows) == 3
    assert {r["symbol"] for r in rows} == {"000001.SZ"}
    assert float(rows[0]["turnover_rate"]) == pytest.approx(0.01)
    assert float(rows[1]["turnover_rate"]) == pytest.approx(0.02)
    assert float(rows[2]["turnover_rate"]) == pytest.approx(0.015)
    assert float(rows[0]["qfq_factor"]) == pytest.approx(0.9)
    assert float(rows[1]["qfq_factor"]) == pytest.approx(0.9)
    assert float(rows[2]["qfq_factor"]) == pytest.approx(1.0)


def test_build_prices_qfq_from_db_is_idempotent(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    db_path = tmp_path / "stock_master.duckdb"
    _seed_db_tables(db_path)
    CuratedLayer(lake).build_prices_qfq_from_db(str(db_path))

    connection = duckdb.connect(str(db_path))
    try:
        connection.execute(
            """
            INSERT INTO market.daily_bar VALUES
            ('000001.SZ', 'stock', '2026-01-08', 'none',
             10, 10, 10, 10, 100, 1000, 0.02, 'trusted', 'hithink', 1);
            INSERT INTO market.adjustment_factor
            VALUES (4, '000001.SZ', '2026-01-08', 'qfq', 1.0, 'hithink_finance_dump', 'v1', now())
            """
        )
    finally:
        connection.close()

    second = CuratedLayer(lake).build_prices_qfq_from_db(str(db_path))

    assert second.row_count == 4
    rows = _read(str(lake.root / "curated" / "prices_qfq" / "**" / "*.parquet"))
    assert len(rows) == 4
    assert rows[-1]["trade_date"] == date(2026, 1, 8)
    assert list((lake.root / "curated").glob("prices_qfq.backup-*")) == []
    assert list((lake.root / "curated").glob(".prices_qfq.replacing-*")) == []


def test_staged_prices_are_not_visible_until_publish(tmp_path) -> None:
    lake = DataLake(tmp_path / "lake")
    db_path = tmp_path / "stock_master.duckdb"
    _seed_db_tables(db_path)
    layer = CuratedLayer(lake)

    staged = layer.build_prices_qfq_from_db(str(db_path), publish=False)

    published_path = lake.root / "curated" / "prices_qfq"
    assert not published_path.exists()
    assert Path(staged.path).name.startswith(".prices_qfq.tmp-")

    published = layer.publish_prices_qfq(staged)

    assert Path(published.path) == published_path.resolve()
    assert published_path.exists()
    assert len(_read(str(published_path / "**" / "*.parquet"))) == 3


def test_publish_rolls_back_if_staged_rename_fails(tmp_path, monkeypatch) -> None:
    from app.lake.curated import _replace_directory

    root = tmp_path / "curated"
    root.mkdir()
    target = root / "prices_qfq"
    source = root / ".prices_qfq.tmp-test"
    target.mkdir()
    source.mkdir()
    (target / "old.parquet").write_bytes(b"old")
    (source / "new.parquet").write_bytes(b"new")
    original_rename = Path.rename

    def fail_staged_rename(path: Path, destination: Path):
        if path == source:
            raise OSError("simulated publish failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_staged_rename)
    with pytest.raises(OSError, match="simulated publish failure"):
        _replace_directory(source, target)

    assert (target / "old.parquet").read_bytes() == b"old"
    assert source.exists()
    assert list(root.glob(".prices_qfq.replacing-*")) == []


def test_publish_retries_transient_windows_directory_lock(tmp_path, monkeypatch) -> None:
    from app.lake.curated import _replace_directory

    root = tmp_path / "curated"
    root.mkdir()
    source = root / ".prices_qfq.tmp-test"
    target = root / "prices_qfq"
    source.mkdir()
    (source / "new.parquet").write_bytes(b"new")
    original_rename = Path.rename
    calls = 0

    def transient_lock(path: Path, destination: Path):
        nonlocal calls
        calls += 1
        if path == source and calls < 3:
            raise PermissionError("simulated transient lock")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", transient_lock)
    _replace_directory(source, target)

    assert calls == 3
    assert (target / "new.parquet").read_bytes() == b"new"


def test_prepared_publish_can_restore_previous_tree_after_activation_failure(
    tmp_path,
) -> None:
    lake = DataLake(tmp_path / "lake")
    layer = CuratedLayer(lake)
    root = lake.root / "curated"
    target = root / "prices_qfq"
    source = root / ".prices_qfq.tmp-test"
    target.mkdir(parents=True)
    source.mkdir()
    (target / "old.parquet").write_bytes(b"old")
    (source / "new.parquet").write_bytes(b"new")
    staged = CuratedDatasetResult(
        dataset="prices_qfq",
        path=str(source),
        row_count=1,
        min_date="2026-01-05",
        max_date="2026-01-05",
        created_at="2026-01-05T00:00:00+00:00",
    )

    transaction = layer.prepare_prices_qfq_publish(staged)
    assert (target / "new.parquet").read_bytes() == b"new"
    assert transaction.retired_path is not None

    layer.rollback_prices_qfq_publish(transaction)

    assert (target / "old.parquet").read_bytes() == b"old"
    assert (source / "new.parquet").read_bytes() == b"new"
    assert not Path(transaction.retired_path).exists()


def test_build_does_not_age_prune_recovery_or_staging_directories(tmp_path) -> None:
    import os
    from time import time

    lake = DataLake(tmp_path / "lake")
    db_path = tmp_path / "stock_master.duckdb"
    _seed_db_tables(db_path)
    root = lake.root / "curated"
    legacy = root / "prices_qfq.backup-old"
    staging = root / ".prices_qfq.tmp-old"
    legacy.mkdir(parents=True)
    staging.mkdir()
    (legacy / "data.parquet").write_bytes(b"recovery")
    (staging / "data.parquet").write_bytes(b"staging")
    old = time() - 3 * 86400
    os.utime(legacy, (old, old))
    os.utime(staging, (old, old))

    CuratedLayer(lake).build_prices_qfq_from_db(str(db_path))

    assert (legacy / "data.parquet").read_bytes() == b"recovery"
    assert (staging / "data.parquet").read_bytes() == b"staging"
