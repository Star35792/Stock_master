import os
import time

import duckdb

DB = "data/stock_master.duckdb"
BIG = [
    ("market", "ix_daily_bar_quality_status"),
    ("market", "ix_daily_bar_status_symbol_adjust_date"),
    ("market", "ix_daily_bar_symbol_adjust_date"),
    ("market", "ix_daily_bar_trade_date_type"),
    ("market", "ix_adjustment_factor_date_type"),
    ("market", "ix_adjustment_factor_symbol_type_date"),
    ("raw", "ix_raw_daily_bar_batch_id"),
    ("raw", "ix_raw_daily_bar_row_hash"),
    ("raw", "ix_raw_daily_bar_source_date"),
    ("raw", "ix_raw_daily_bar_source_symbol_adjust_date"),
    ("raw", "ix_raw_daily_bar_symbol_adjust_date"),
    ("raw", "ix_raw_daily_bar_symbol_date"),
]
before = os.path.getsize(DB)
con = duckdb.connect(DB)
ok = []
fail = []
t = time.perf_counter()
for sch, nm in BIG:
    try:
        con.execute(f"DROP INDEX {sch}.{nm}")
        ok.append(f"{sch}.{nm}")
    except Exception as e:
        fail.append((f"{sch}.{nm}", str(e)[:80]))
con.execute("CHECKPOINT")
con.close()
after = os.path.getsize(DB)
print(f"删除 {len(ok)}/{len(BIG)}, 耗时 {time.perf_counter() - t:.1f}s")
if fail:
    print("失败明细:")
    for n, e in fail:
        print(f"  {n}: {e}")
before_gib = before / 2**30
after_gib = after / 2**30
saved_gib = (before - after) / 2**30
print(f"库大小: {before_gib:.3f} -> {after_gib:.3f} GiB (节省 {saved_gib:.3f} GiB)")
con = duckdb.connect(DB, read_only=True)
print(f"剩余索引: {con.execute('SELECT count(*) FROM duckdb_indexes()').fetchone()[0]} (原 93)")
unique_count = con.execute("SELECT count(*) FROM duckdb_indexes() WHERE is_unique").fetchone()[0]
print(f"剩余 UNIQUE: {unique_count} (原 15, 应不变)")
print(
    f"market.daily_bar 行数: {con.execute('SELECT count(*) FROM market.daily_bar').fetchone()[0]:,}"
)
print(f"raw.daily_bar 行数: {con.execute('SELECT count(*) FROM raw.daily_bar').fetchone()[0]:,}")
con.close()
