import duckdb, os, time, sys

SRC = "data/stock_master.duckdb"
DST = "data/stock_master.rebuild.tmp.duckdb"
for p in (DST, DST + ".wal"):
    if os.path.exists(p):
        os.remove(p)

t0 = time.time()
con = duckdb.connect()  # 内存库作为协调者
con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
con.execute(f"ATTACH '{DST}' AS dst")
print(f"[{time.time()-t0:6.1f}s] attached. 开始 COPY FROM DATABASE ...", flush=True)
try:
    con.execute("COPY FROM DATABASE src TO dst")
except Exception as e:
    print("COPY FAILED:", e, flush=True)
    sys.exit(1)
print(f"[{time.time()-t0:6.1f}s] COPY 完成", flush=True)
con.execute("CHECKPOINT dst")
print(f"[{time.time()-t0:6.1f}s] checkpoint 完成", flush=True)
con.close()
sz = os.path.getsize(DST)
print(f"新库大小: {sz/2**30:.3f} GiB ({sz:,} bytes)")
print(f"原库大小: {os.path.getsize(SRC)/2**30:.3f} GiB")
print(f"总耗时: {time.time()-t0:.1f}s")
