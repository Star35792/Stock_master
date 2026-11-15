import duckdb

from scripts.ingest import _rebuild_curated_qfq_supplement as rebuild


def test_supplement_rebuild_replaces_old_curated_without_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "warm" / "curated"
    old = root / "prices_qfq"
    old.mkdir(parents=True)
    (old / "old.parquet").write_bytes(b"old")
    database = tmp_path / "data" / "stock_master.duckdb"
    con = duckdb.connect(str(database))
    try:
        con.execute("CREATE SCHEMA market")
        con.execute("""
            CREATE TABLE market.daily_bar (
                symbol VARCHAR, trade_date DATE,
                open DECIMAL(18,4), high DECIMAL(18,4),
                low DECIMAL(18,4), close DECIMAL(18,4),
                volume BIGINT, amount DECIMAL(20,4), turnover_rate DECIMAL(12,6),
                quality_status VARCHAR, adjust_type VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE market.adjustment_factor (
                id BIGINT, symbol VARCHAR, trade_date DATE, adjust_type VARCHAR,
                source_name VARCHAR, factor DECIMAL(18,8), updated_at TIMESTAMPTZ
            )
        """)
        con.execute("""
            INSERT INTO market.daily_bar VALUES
            ('000001.SZ', '2026-11-11', 10, 11, 9, 10, 100, 1000, 0.01, 'trusted', 'qfq')
        """)
    finally:
        con.close()

    rebuild.main()

    current = root / "prices_qfq"
    assert not (current / "old.parquet").exists()
    assert list(current.rglob("*.parquet"))
    assert list(root.glob("prices_qfq.prev-*")) == []
    assert list(root.glob(".prices_qfq.replacing-*")) == []
