"""Regression tests for the warm-layer release resolution rules.

The warm layer had no unit coverage at all, which hid three defects that a
newly registered table would have triggered across every existing release:

* ``table_globs`` fell back to the requested release's own path when a table
  was absent, so DuckDB raised ``IO Error: No files found`` and the research
  read path broke for releases that do not carry the table.
* ``base_release_tag`` was consulted for one level only, so a release stacked
  on a release could not resolve the tables that live further up the chain.
* ``latest_release_tag`` ignored ``all_ok``, so a failed export could become
  the fact source.

These tests build real Parquet releases in a temporary directory instead of
mocking the store, because the defects were all about path resolution.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from app.repositories.warm_layer import WarmLayerStore

_TRADE_DATES = (date(2026, 1, 5), date(2026, 1, 6))


def _write_release(
    root: Path,
    tag: str,
    *,
    tables: dict[str, list[tuple[str, date, float]]],
    base: str | None = None,
    all_ok: bool = True,
    extra_columns: dict[str, list[tuple[str, str]]] | None = None,
) -> Path:
    """Publish a release directory containing the given parquet partitions.

    ``extra_columns`` lets a release export a table with additional columns
    (the ``factor.factor_value`` export once carried a surrogate ``id``).
    Each entry is a ``(column_name, sql_expression)`` pair.
    """
    release = root / "exported" / tag
    release.mkdir(parents=True, exist_ok=True)
    for table, rows in tables.items():
        if not rows:
            # A release that carries no data for a table simply does not
            # publish that table; the reader must tolerate its absence.
            continue
        schema, _, name = table.partition(".")
        target = release / schema / name
        target.mkdir(parents=True, exist_ok=True)
        extra = (extra_columns or {}).get(table, [])
        con = duckdb.connect(":memory:")
        con.execute(
            "CREATE TABLE t (symbol VARCHAR, trade_date DATE, value DOUBLE)"
        )
        con.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
        required_keys = {
            "market.daily_bar": "'none' AS adjust_type",
            "market.adjustment_factor": (
                "'qfq' AS adjust_type, 'test' AS source_name, 'v1' AS version"
            ),
            "factor.factor_value": (
                "'test_factor' AS factor_name, 'v1' AS factor_version"
            ),
        }.get(table)
        suffix = f", {required_keys}" if required_keys else ""
        select = f"SELECT *{suffix} FROM t"
        if extra:
            # Materialise the extra columns with deterministic values so the
            # two releases differ in shape but not in row count.
            assignments = ", ".join(f"{expr} AS {col}" for col, expr in extra)
            select = (
                f"SELECT {assignments}, symbol, trade_date, value{suffix} FROM t"
            )
        con.execute(
            f"COPY ({select} ORDER BY symbol, trade_date) "
            f"TO '{target.as_posix()}/data_0.parquet' (FORMAT PARQUET)"
        )
        con.close()
    summary: dict[str, object] = {"release_tag": tag, "all_ok": all_ok}
    if base:
        summary["base_release_tag"] = base
    (release / "export_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    return release


@pytest.fixture
def warm_root(tmp_path: Path) -> Path:
    root = tmp_path / "warm"
    (root / "exported").mkdir(parents=True)
    return root


def test_table_absent_from_chain_is_skipped_not_fatal(warm_root: Path) -> None:
    """A table missing from the whole chain must not break the connection.

    This is the regression that a newly registered warm table would have
    caused: the release that does not carry the table still has to be
    readable for every table it does carry.
    """
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    store = WarmLayerStore(warm_root)
    con = store.connect(release_tag="20260101-0000", attach_control=False)

    assert con.execute("SELECT count(*) FROM market.daily_bar").fetchone()[0] == 1
    # The table is simply not exposed; the connection stays usable.
    with pytest.raises(duckdb.Error):
        con.execute("SELECT * FROM market.valuation_history").fetchall()
    con.close()


def test_release_chain_resolves_tables_from_grandparent(warm_root: Path) -> None:
    """A table living two levels up must still resolve through the chain."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"factor.factor_value": [("000001.SZ", _TRADE_DATES[0], 2.0)]},
        base="20260101-0000",
    )
    _write_release(
        warm_root,
        "20260103-0000",
        tables={"market.adjustment_factor": [("000001.SZ", _TRADE_DATES[0], 3.0)]},
        base="20260102-0000",
    )

    store = WarmLayerStore(warm_root)
    assert store.release_lineage("20260103-0000") == [
        "20260103-0000",
        "20260102-0000",
        "20260101-0000",
    ]

    con = store.connect(release_tag="20260103-0000", attach_control=False)
    assert con.execute("SELECT count(*) FROM market.daily_bar").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM factor.factor_value").fetchone()[0] == 1
    assert (
        con.execute("SELECT count(*) FROM market.adjustment_factor").fetchone()[0] == 1
    )
    con.close()


def test_release_cycle_does_not_hang(warm_root: Path) -> None:
    """A malformed parent cycle must terminate instead of looping forever."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
        base="20260102-0000",
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"market.adjustment_factor": [("000001.SZ", _TRADE_DATES[0], 3.0)]},
        base="20260101-0000",
    )

    store = WarmLayerStore(warm_root)
    lineage = store.release_lineage("20260101-0000")
    assert lineage[0] == "20260101-0000"
    assert len(lineage) == len(set(lineage))


def test_latest_release_skips_failed_export(warm_root: Path) -> None:
    """A failed release must never become the research fact source."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"market.daily_bar": []},
        all_ok=False,
    )

    store = WarmLayerStore(warm_root)
    assert store.latest_release_tag == "20260101-0000"


def test_latest_release_is_none_when_every_release_failed(warm_root: Path) -> None:
    """With no successful release there is no fact source to expose."""
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"market.daily_bar": []},
        all_ok=False,
    )

    store = WarmLayerStore(warm_root)
    assert store.latest_release_tag is None


def test_table_globs_returns_only_existing_directories(warm_root: Path) -> None:
    """Missing tables yield an empty list instead of a fabricated path."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )

    store = WarmLayerStore(warm_root)
    globs = store.table_globs("market", "daily_bar", "20260101-0000")
    assert len(globs) == 1
    assert globs[0].exists()

    assert store.table_globs("research", "valuation_history", "20260101-0000") == []


def test_warm_table_is_visible_with_and_without_control_attachment(
    warm_root: Path, tmp_path: Path
) -> None:
    """A Parquet-only table must be visible in both connection modes.

    The attached mode used to enumerate the control-plane catalog only, so a
    warm dataset without a control-plane counterpart was invisible to every
    attached caller.
    """
    _write_release(
        warm_root,
        "20260101-0000",
        tables={
            "market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)],
            "research.valuation_history": [("000001.SZ", _TRADE_DATES[0], 4.0)],
        },
    )
    control_db = tmp_path / "control.duckdb"
    con0 = duckdb.connect(str(control_db))
    con0.execute("CREATE SCHEMA market")
    # The control-plane counterpart must carry the same date column as the
    # warm table: the attached connection unions the Parquet rows with the
    # control-plane rows that are newer than the Parquet cutoff.
    con0.execute(
        "CREATE TABLE market.daily_bar "
        "(symbol VARCHAR, trade_date DATE, value DOUBLE, adjust_type VARCHAR)"
    )
    con0.close()

    store = WarmLayerStore(warm_root)
    for attach in (False, True):
        con = store.connect(
            control_db_path=control_db,
            release_tag="20260101-0000",
            attach_control=attach,
        )
        count = con.execute("SELECT count(*) FROM research.valuation_history").fetchone()[0]
        assert count == 1, f"attach_control={attach}"
        con.close()


def test_release_chain_with_differing_column_sets_is_readable(warm_root: Path) -> None:
    """Two releases exporting different column sets must still union.

    The real ``factor.factor_value`` export carried a surrogate ``id`` column
    before that column was dropped.  Reusing the first glob's column list for
    every glob made the UNION ALL reference a column the other release does
    not have, and DuckDB raised
    ``Binder Error: Referenced column "id" not found in FROM clause!``.
    """
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"factor.factor_value": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
        extra_columns={"factor.factor_value": [("id", "CAST(1 AS BIGINT)")]},
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"factor.factor_value": [("000002.SZ", _TRADE_DATES[1], 2.0)]},
        base="20260101-0000",
    )

    store = WarmLayerStore(warm_root)
    con = store.connect(release_tag="20260102-0000", attach_control=False)
    rows = con.execute(
        "SELECT symbol, value, id FROM factor.factor_value ORDER BY symbol"
    ).fetchall()
    assert len(rows) == 2
    # The release that lacks the column contributes NULL for it.
    assert rows[0][2] is not None
    assert rows[1][2] is None
    con.close()


def test_snapshot_table_supersedes_same_table_in_parent(warm_root: Path) -> None:
    """A complete export must not be UNIONed with an older full snapshot."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"market.daily_bar": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    release = _write_release(
        warm_root,
        "20260102-0000",
        tables={
            "market.daily_bar": [
                ("000001.SZ", _TRADE_DATES[0], 1.0),
                ("000002.SZ", _TRADE_DATES[1], 2.0),
            ]
        },
        base="20260101-0000",
    )
    summary_path = release / "export_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["tables"] = [
        {"schema": "market", "table": "daily_bar", "mode": "snapshot"}
    ]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")

    store = WarmLayerStore(warm_root)
    assert len(store.table_globs("market", "daily_bar", "20260102-0000")) == 1
    con = store.connect(release_tag="20260102-0000", attach_control=False)
    assert con.execute("SELECT count(*) FROM market.daily_bar").fetchone()[0] == 2
    con.close()


def test_delta_release_overrides_parent_on_business_key(warm_root: Path) -> None:
    """Delta rows replace parent keys instead of duplicating observations."""
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"factor.factor_value": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={
            "factor.factor_value": [
                ("000001.SZ", _TRADE_DATES[0], 9.0),
                ("000002.SZ", _TRADE_DATES[1], 2.0),
            ]
        },
        base="20260101-0000",
    )

    store = WarmLayerStore(warm_root)
    con = store.connect(release_tag="20260102-0000", attach_control=False)
    rows = con.execute(
        "SELECT symbol, value FROM factor.factor_value ORDER BY symbol"
    ).fetchall()
    assert rows == [("000001.SZ", 9.0), ("000002.SZ", 2.0)]
    con.close()


def test_explicit_base_release_does_not_reorder_the_lineage(warm_root: Path) -> None:
    """An explicit base must not become the head of the glob chain.

    The lineage is ordered newest-first because the newest release defines the
    view's column set.  Prepending the base release put an ancestor's columns
    first, which is how the ``id`` binder error resurfaced even after the
    column alignment was fixed.
    """
    _write_release(
        warm_root,
        "20260101-0000",
        tables={"factor.factor_value": [("000001.SZ", _TRADE_DATES[0], 1.0)]},
    )
    _write_release(
        warm_root,
        "20260102-0000",
        tables={"factor.factor_value": [("000002.SZ", _TRADE_DATES[1], 2.0)]},
        base="20260101-0000",
    )

    store = WarmLayerStore(warm_root, base_release_tag="20260101-0000")
    globs = store.table_globs("factor", "factor_value", "20260102-0000")
    assert [glob.parent.parent.name for glob in globs] == [
        "20260102-0000",
        "20260101-0000",
    ]

    con = store.connect(release_tag="20260102-0000", attach_control=False)
    assert (
        con.execute("SELECT count(*) FROM factor.factor_value").fetchone()[0] == 2
    )
    con.close()
