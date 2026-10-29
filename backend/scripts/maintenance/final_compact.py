import os
import time

import duckdb

SRC = "data/stock_master.duckdb"
DST = "data/compact_test.tmp.duckdb"
for p in (DST, DST + ".wal"):
    if os.path.exists(p):
        os.remove(p)
t0 = time.perf_counter()
con = duckdb.connect()
con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
con.execute(f"ATTACH '{DST}' AS dst")
tabs = con.execute("SELECT schema_name,table_name FROM duckdb_tables() ORDER BY 1,2").fetchall()
for sch, tbl in tabs:
    con.execute(f'CREATE SCHEMA IF NOT EXISTS dst."{sch}"')
    con.execute(
        f'CREATE TABLE dst."{sch}"."{tbl}" AS SELECT * FROM src."{sch}"."{tbl}" WHERE false'
    )
print(f"[{time.perf_counter() - t0:.0f}s] 表结构已建，开始灌数据...", flush=True)
for sch, tbl in tabs:
    con.execute(f'INSERT INTO dst."{sch}"."{tbl}" SELECT * FROM src."{sch}"."{tbl}"')
con.execute("CHECKPOINT dst")
con.close()
sz = os.path.getsize(DST)
cur = os.path.getsize(SRC)
print(f"\n仅表数据(无索引无视图): {sz / 2**30:.3f} GiB")
print(f"当前库(81索引):        {cur / 2**30:.3f} GiB")
print(f"可再省:                {(cur - sz) / 2**30:.3f} GiB")
print(f"耗时: {time.perf_counter() - t0:.0f}s")
