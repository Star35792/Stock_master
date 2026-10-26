"""Regression tests for the valuation-history read paths.

The valuation history is the first dataset that lives *only* in the Parquet
warm layer, so its read path had no coverage at all.  Two things can go wrong
and both have to fail closed rather than silently return a thin result:

* the warm path must raise when the bound release does not carry
  ``research.valuation_history`` — a zero-row answer would look like "the
  factor has no information" instead of "the data was never published";
* the ORM twin and the warm twin must answer with the same column names and
  the same ordering, because the research contract hashes a normalized slice
  in which the row order is part of reproducibility.

The tests build a real Parquet release instead of mocking the store, because
the defects they guard against are about table resolution.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from app.repositories.research_read import WarmResearchReadRepository
from app.repositories.warm_layer import WarmLayerStore

_TRADE_DATES = (date(2026, 1, 5), date(2026, 1, 6))

_VALUATION_COLUMNS: tuple[str, ...] = (
    "symbol",
    "trade_date",
    "close",
    "pe_ttm",
    "pb_mrq",
    "ps_ttm",
    "total_market_cap",
    "total_shares",
    "free_shares_a",
    "float_market_cap",
    "change_rate",
    "pit_verified",
    "source_name",
    "observed_at",
)


def _write_valuation_release(root: Path, tag: str, *, with_table: bool) -> Path:
    """Publish a release that optionally carries the valuation history."""
    release = root / "exported" / tag
    release.mkdir(parents=True, exist_ok=True)
    if with_table:
        target = release / "research" / "valuation_history"
        target.mkdir(parents=True, exist_ok=True)
        ddl = ", ".join(
            f"{name} {_TYPE}"
            for name, _TYPE in zip(
                _VALUATION_COLUMNS,
                (
                    "VARCHAR",
                    "DATE",
                    "DECIMAL(18,4)",
                    "DECIMAL(24,10)",
                    "DECIMAL(24,10)",
                    "DECIMAL(24,10)",
                    "DECIMAL(30,6)",
                    "DECIMAL(30,6)",
                    "DECIMAL(30,6)",
                    "DECIMAL(30,6)",
                    "DECIMAL(18,8)",
                    "BOOLEAN",
                    "VARCHAR",
                    "VARCHAR",
                ),
                strict=True,
            )
        )
        con = duckdb.connect(":memory:")
        try:
            con.execute(f"CREATE TABLE valuation_history ({ddl})")
            con.execute(
                """
                INSERT INTO valuation_history VALUES
                    ('000001.SZ', DATE '2026-01-05', 10.1234, 12.5, 1.5, 2.5,
                     1000000, 100000, 80000, 900000, 1.25, FALSE,
                     'eastmoney', '2026-01-06T00:00:00+00:00'),
                    ('000002.SZ', DATE '2026-01-05', 20.5000, 8.0, 0.9, 1.1,
                     2000000, 200000, 150000, 1800000, -0.50, FALSE,
                     'eastmoney', '2026-01-06T00:00:00+00:00'),
                    ('000001.SZ', DATE '2026-01-06', 10.5000, 12.0, 1.6, 2.6,
                     1040000, 100000, 80000, 900000, 3.70, FALSE,
                     'eastmoney', '2026-01-07T00:00:00+00:00')
                """
            )
            con.execute(
                "COPY (SELECT * FROM valuation_history "
                "ORDER BY trade_date, symbol) "
                f"TO '{target.as_posix()}/data_0.parquet' (FORMAT PARQUET)"
            )
        finally:
            con.close()
    (release / "export_summary.json").write_text(
        json.dumps({"release_tag": tag, "all_ok": True}), encoding="utf-8"
    )
    return release


@pytest.fixture
def store_with_history(tmp_path: Path) -> WarmLayerStore:
    _write_valuation_release(tmp_path, "with-history", with_table=True)
    return WarmLayerStore(tmp_path)


@pytest.fixture
def store_without_history(tmp_path: Path) -> WarmLayerStore:
    _write_valuation_release(tmp_path, "without-history", with_table=False)
    return WarmLayerStore(tmp_path)


def test_reads_rows_when_release_carries_the_table(
    store_with_history: WarmLayerStore,
) -> None:
    con = store_with_history.connect(
        release_tag="with-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        rows = repository.list_valuation_history(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
        )
    finally:
        con.close()
    assert len(rows) == 3
    assert [row.symbol for row in rows] == ["000001.SZ", "000002.SZ", "000001.SZ"]
    assert [row.trade_date for row in rows] == [
        date(2026, 1, 5),
        date(2026, 1, 5),
        date(2026, 1, 6),
    ]
    assert rows[0].close == Decimal("10.1234")
    assert rows[0].pe_ttm == Decimal("12.5")
    # Every backfilled row is a late observation, so the flag must be false.
    assert rows[0].pit_verified is False


def test_orders_by_trade_date_then_symbol(store_with_history: WarmLayerStore) -> None:
    con = store_with_history.connect(
        release_tag="with-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        rows = repository.list_valuation_history(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
        )
    finally:
        con.close()
    keys = [(row.trade_date, row.symbol) for row in rows]
    assert keys == sorted(keys)


def test_filters_by_symbol(store_with_history: WarmLayerStore) -> None:
    con = store_with_history.connect(
        release_tag="with-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        rows = repository.list_valuation_history(
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            symbols=["000001.SZ"],
        )
    finally:
        con.close()
    assert {row.symbol for row in rows} == {"000001.SZ"}
    assert len(rows) == 2


def test_raises_when_release_lacks_the_table(store_without_history: WarmLayerStore) -> None:
    """A missing table must fail closed, not look like an empty dataset."""
    con = store_without_history.connect(
        release_tag="without-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        with pytest.raises(ValueError, match="research.valuation_history"):
            repository.list_valuation_history(
                start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
            )
    finally:
        con.close()


def test_coverage_reports_the_window(store_with_history: WarmLayerStore) -> None:
    con = store_with_history.connect(
        release_tag="with-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        coverage = repository.valuation_history_coverage(
            start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
        )
    finally:
        con.close()
    assert coverage["row_count"] == 3
    assert coverage["trading_days"] == 2
    assert coverage["symbol_count"] == 2
    assert coverage["min_date"] == date(2026, 1, 5)
    assert coverage["max_date"] == date(2026, 1, 6)


def test_coverage_raises_when_release_lacks_the_table(
    store_without_history: WarmLayerStore,
) -> None:
    con = store_without_history.connect(
        release_tag="without-history", read_only=True, attach_control=False
    )
    try:
        repository = WarmResearchReadRepository(con)
        with pytest.raises(ValueError, match="research.valuation_history"):
            repository.valuation_history_coverage(
                start_date=date(2026, 1, 1), end_date=date(2026, 1, 31)
            )
    finally:
        con.close()
