import json

import duckdb

from scripts.maintenance import export_warm_layer


def _source_db(path) -> None:
    con = duckdb.connect(str(path))
    try:
        con.execute("CREATE SCHEMA market")
        con.execute("CREATE TABLE market.daily_bar (symbol VARCHAR, trade_date DATE)")
        con.execute("INSERT INTO market.daily_bar VALUES ('000001.SZ', '2026-11-11')")
    finally:
        con.close()


def test_failed_export_reclaims_its_unpublished_release(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.duckdb"
    warm_root = tmp_path / "warm" / "exported"
    _source_db(source)
    monkeypatch.setattr(
        export_warm_layer,
        "EXPORT_TABLES",
        [("market", "daily_bar", "trade_date"), ("missing", "table", "trade_date")],
    )

    result = export_warm_layer.main(
        ["--db-path", str(source), "--warm-root", str(warm_root), "--release-tag", "failed"]
    )

    assert result == 1
    assert not (warm_root / "failed").exists()


def test_failed_row_count_verification_reclaims_release(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.duckdb"
    warm_root = tmp_path / "warm" / "exported"
    _source_db(source)
    monkeypatch.setattr(export_warm_layer, "EXPORT_TABLES", [
        ("market", "daily_bar", "trade_date"), ("missing", "table", "trade_date")
    ])
    original_export = export_warm_layer.export_table
    attempted = []

    def record_export(*args, **kwargs):
        attempted.append((kwargs["schema"], kwargs["table"]))
        return original_export(*args, **kwargs)

    monkeypatch.setattr(export_warm_layer, "export_table", record_export)
    monkeypatch.setattr(export_warm_layer, "verify_export", lambda *_: False)

    assert export_warm_layer.main(
        ["--db-path", str(source), "--warm-root", str(warm_root), "--release-tag", "bad-count"]
    ) == 1
    assert attempted == [("market", "daily_bar")]
    assert not (warm_root / "bad-count").exists()


def test_cleanup_failure_never_marks_release_as_valid(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.duckdb"
    warm_root = tmp_path / "warm" / "exported"
    _source_db(source)
    monkeypatch.setattr(export_warm_layer, "EXPORT_TABLES", [
        ("market", "daily_bar", "trade_date"), ("missing", "table", "trade_date")
    ])
    monkeypatch.setattr(
        export_warm_layer.shutil, "rmtree", lambda *_: (_ for _ in ()).throw(OSError("denied"))
    )

    assert export_warm_layer.main(
        ["--db-path", str(source), "--warm-root", str(warm_root), "--release-tag", "orphan"]
    ) == 1
    assert (warm_root / "orphan").exists()
    assert not (warm_root / "orphan" / "export_summary.json").exists()


def test_existing_release_is_not_overwritten_or_removed(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.duckdb"
    warm_root = tmp_path / "warm" / "exported"
    _source_db(source)
    monkeypatch.setattr(
        export_warm_layer, "EXPORT_TABLES", [("market", "daily_bar", "trade_date")]
    )
    args = ["--db-path", str(source), "--warm-root", str(warm_root), "--release-tag", "done"]

    assert export_warm_layer.main(args) == 0
    summary = warm_root / "done" / "export_summary.json"
    before = summary.read_bytes()
    assert json.loads(before)["all_ok"] is True

    assert export_warm_layer.main(args) == 1
    assert summary.read_bytes() == before
