import json
from datetime import date

import duckdb
import pytest

from scripts.maintenance.slim_metadata_db import (
    _missing_warm_tables,
    _verify_cold_coverage,
    main,
)


def test_slim_uses_complete_release_lineage_and_ignores_failed_newer_export(tmp_path) -> None:
    exported = tmp_path / "exported"
    parent = exported / "20261111-100000"
    child = exported / "20261111-110000"
    failed = exported / "20261111-120000"
    for release in (parent, child, failed):
        release.mkdir(parents=True)
    (parent / "export_summary.json").write_text('{"all_ok": true}', encoding="utf-8")
    (child / "export_summary.json").write_text(
        json.dumps({"all_ok": True, "base_release_tag": parent.name}), encoding="utf-8"
    )
    (failed / "export_summary.json").write_text('{"all_ok": false}', encoding="utf-8")
    parquet_dir = parent / "market" / "daily_bar"
    parquet_dir.mkdir(parents=True)
    tables = (("market", "daily_bar", "trade_date"),)
    assert _missing_warm_tables(tmp_path, tables) == [
        f"market.daily_bar (no Parquet in release chain {child.name})"
    ]
    (parquet_dir / "part.parquet").touch()

    assert _missing_warm_tables(tmp_path, tables) == []

    (parent / "export_summary.json").write_text('{"all_ok": false}', encoding="utf-8")
    assert _missing_warm_tables(tmp_path, tables) == [
        f"release chain contains incomplete release(s): {parent.name}"
    ]


def test_slim_refuses_cold_rows_missing_or_changed_in_warm_release(tmp_path) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA market")
        con.execute(
            "CREATE TABLE market.daily_bar (symbol VARCHAR, trade_date DATE, "
            "adjust_type VARCHAR, close DECIMAL(12, 2))"
        )
        con.execute(
            "INSERT INTO market.daily_bar VALUES "
            "('A', DATE '2020-01-01', 'none', 1.00), "
            "('B', DATE '2020-01-02', 'none', 2.00)"
        )

    release = tmp_path / "warm" / "exported" / "20261111-100000"
    parquet_dir = release / "market" / "daily_bar"
    parquet_dir.mkdir(parents=True)
    (release / "export_summary.json").write_text('{"all_ok": true}', encoding="utf-8")
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute(
            f"COPY (SELECT * FROM market.daily_bar WHERE symbol = 'A') "
            f"TO '{(parquet_dir / 'part-a.parquet').as_posix()}' (FORMAT PARQUET)"
        )

    tables = (("market", "daily_bar", "trade_date"),)
    with pytest.raises(ValueError, match="does not cover all 2 cold rows"):
        _verify_cold_coverage(path, tmp_path / "warm", date(2021, 1, 1), tables)

    part_b = parquet_dir / "part-b.parquet"
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute(
            "COPY (SELECT symbol, trade_date, adjust_type, "
            "3.00::DECIMAL(12, 2) AS close FROM market.daily_bar WHERE symbol = 'B') "
            f"TO '{part_b.as_posix()}' (FORMAT PARQUET)"
        )
    with pytest.raises(ValueError, match="matching values and lineage"):
        _verify_cold_coverage(path, tmp_path / "warm", date(2021, 1, 1), tables)

    part_b.unlink()
    with duckdb.connect(str(path), read_only=True) as con:
        con.execute(
            f"COPY (SELECT * FROM market.daily_bar WHERE symbol = 'B') "
            f"TO '{part_b.as_posix()}' (FORMAT PARQUET)"
        )
    _verify_cold_coverage(path, tmp_path / "warm", date(2021, 1, 1), tables)


def test_slim_can_verify_unpublished_release_before_activation(tmp_path) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SCHEMA market")
        con.execute(
            "CREATE TABLE market.daily_bar (symbol VARCHAR, trade_date DATE, "
            "adjust_type VARCHAR, close DECIMAL(12, 2))"
        )
        con.execute("INSERT INTO market.daily_bar VALUES ('A', DATE '2020-01-01', 'none', 2.00)")

    exported = tmp_path / "warm" / "exported"
    parent = exported / "20261111-100000"
    staged = exported / "20261111-110000"
    for release, close in ((parent, "1.00"), (staged, "2.00")):
        parquet = release / "market" / "daily_bar" / "part.parquet"
        parquet.parent.mkdir(parents=True)
        with duckdb.connect(":memory:") as con:
            con.execute(
                "COPY (SELECT 'A'::VARCHAR AS symbol, DATE '2020-01-01' AS trade_date, "
                f"'none'::VARCHAR AS adjust_type, {close}::DECIMAL(12, 2) AS close) "
                f"TO '{parquet.as_posix()}' (FORMAT PARQUET)"
            )
    (parent / "export_summary.json").write_text(
        json.dumps(
            {
                "all_ok": True,
                "tables": [{"schema": "market", "table": "daily_bar", "mode": "snapshot"}],
            }
        ),
        encoding="utf-8",
    )
    (staged / "export_summary.json").write_text(
        json.dumps({"all_ok": False, "base_release_tag": parent.name}), encoding="utf-8"
    )

    tables = (("market", "daily_bar", "trade_date"),)
    with pytest.raises(ValueError, match="does not cover"):
        _verify_cold_coverage(path, tmp_path / "warm", date(2021, 1, 1), tables)
    _verify_cold_coverage(
        path, tmp_path / "warm", date(2021, 1, 1), tables, release_tag=staged.name
    )


def test_slim_dry_run_refuses_stale_warm_values_without_touching_db(tmp_path, monkeypatch) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
        con.execute("INSERT INTO misc VALUES (1)")

    def stale_warm(*_args):
        raise ValueError("warm values are stale")

    monkeypatch.setattr("scripts.maintenance.slim_metadata_db._verify_cold_coverage", stale_warm)
    assert main(["--db-path", str(path), "--dry-run"]) == 1
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT id FROM misc").fetchall() == [(1,)]


def test_slim_preserves_control_rows_and_regular_index(tmp_path) -> None:
    """A completed file swap keeps both facts and query indexes."""
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE SEQUENCE misc_id_seq START 3")
        con.execute("CREATE TABLE misc (id INTEGER DEFAULT nextval('misc_id_seq'), note VARCHAR)")
        con.execute("INSERT INTO misc VALUES (1, 'one'), (2, 'two')")
        con.execute("CREATE INDEX ix_misc_note ON misc (note)")
        con.execute("CHECKPOINT")

    assert main(["--db-path", str(path)]) == 0

    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM misc").fetchone()[0] == 2
        assert con.execute(
            "SELECT count(*) FROM duckdb_indexes() WHERE index_name = 'ix_misc_note'"
        ).fetchone()[0] == 1
        assert con.execute(
            "SELECT count(*) FROM duckdb_sequences() WHERE sequence_name = 'misc_id_seq'"
        ).fetchone()[0] == 1
    with duckdb.connect(str(path)) as con:
        assert con.execute(
            "INSERT INTO misc (note) VALUES ('three') RETURNING id"
        ).fetchone()[0] == 3
    assert not path.with_suffix(".duckdb.slimming").exists()
    assert not path.with_suffix(".duckdb.prev").exists()


def test_slim_refuses_another_writer(tmp_path) -> None:
    from app.utils.duckdb_write_lock import duckdb_write_lock

    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
    with duckdb_write_lock(tmp_path / "duckdb_write.lock", timeout_seconds=0):
        assert main(["--db-path", str(path)]) == 1
    assert not path.with_suffix(".duckdb.slimming").exists()


def test_slim_reports_previous_artifact_without_removing_it(tmp_path) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
    previous = path.with_suffix(".duckdb.slimming")
    previous.write_bytes(b"unfinished")

    assert main(["--db-path", str(path)]) == 1
    assert previous.read_bytes() == b"unfinished"


def test_slim_failed_index_copy_reclaims_unpublished_file(tmp_path, monkeypatch) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
        con.execute("INSERT INTO misc VALUES (1)")

    def fail_index(*_args):
        raise RuntimeError("index copy failed")

    monkeypatch.setattr("scripts.maintenance.slim_metadata_db._copy_indexes", fail_index)
    with pytest.raises(RuntimeError, match="index copy failed"):
        main(["--db-path", str(path)])

    assert path.is_file()
    assert not path.with_suffix(".duckdb.slimming").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM misc").fetchone()[0] == 1


def test_slim_rejects_missing_row_before_swap(tmp_path, monkeypatch) -> None:
    from scripts.maintenance import slim_metadata_db

    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
        con.execute("INSERT INTO misc VALUES (1), (2)")

    copy_control = slim_metadata_db._copy_control_tables

    def omit_row(source, target):
        copied = copy_control(source, target)
        target.execute("DELETE FROM misc WHERE id = 2")
        return copied

    monkeypatch.setattr(slim_metadata_db, "_copy_control_tables", omit_row)
    with pytest.raises(ValueError, match="row count differs"):
        main(["--db-path", str(path)])

    assert not path.with_suffix(".duckdb.slimming").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT id FROM misc ORDER BY id").fetchall() == [(1,), (2,)]


def test_slim_rejects_changed_default_before_swap(tmp_path, monkeypatch) -> None:
    from scripts.maintenance import slim_metadata_db

    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER DEFAULT 3)")

    restore_defaults = slim_metadata_db._restore_all_defaults

    def change_default(source, target):
        restore_defaults(source, target)
        target.execute("ALTER TABLE misc ALTER COLUMN id SET DEFAULT 99")

    monkeypatch.setattr(slim_metadata_db, "_restore_all_defaults", change_default)
    with pytest.raises(ValueError, match="column defaults missing"):
        main(["--db-path", str(path)])

    assert not path.with_suffix(".duckdb.slimming").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        default = con.execute(
            "SELECT column_default FROM information_schema.columns WHERE table_name = 'misc'"
        ).fetchone()[0]
        assert default == "3"


def test_slim_checkpoint_failure_restores_original(tmp_path, monkeypatch) -> None:
    path = tmp_path / "control.duckdb"
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE misc (id INTEGER)")
        con.execute("INSERT INTO misc VALUES (1)")

    def fail_checkpoint(engine):
        with engine.begin() as conn:
            conn.exec_driver_sql("INSERT INTO misc VALUES (2)")
        raise RuntimeError("checkpoint failed")

    monkeypatch.setattr(
        "scripts.maintenance.slim_metadata_db._checkpoint_required", fail_checkpoint
    )
    with pytest.raises(RuntimeError, match="checkpoint failed"):
        main(["--db-path", str(path)])

    assert path.is_file()
    assert not path.with_suffix(".duckdb.prev").exists()
    assert not path.with_suffix(".duckdb.wal").exists()
    with duckdb.connect(str(path), read_only=True) as con:
        assert con.execute("SELECT count(*) FROM misc").fetchone()[0] == 1
