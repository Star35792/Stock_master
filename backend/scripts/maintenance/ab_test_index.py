import os
import statistics
import time

import duckdb

SRC = "data/stock_master.duckdb"
AB = "data/index_ab_test.tmp.duckdb"
for p in (AB, AB + ".wal"):
    if os.path.exists(p):
        os.remove(p)
con = duckdb.connect()
con.execute(f"ATTACH '{SRC}' AS src (READ_ONLY)")
con.execute(f"ATTACH '{AB}' AS dst")
con.execute("COPY FROM DATABASE src TO dst")
con.execute("CHECKPOINT dst")
con.close()
print(f"A 库(含索引) 建立: {os.path.getsize(AB) / 2**30:.3f} GiB")

Q = [
    (
        "Q1 单只股票K线",
        "SELECT trade_date,close,pct_change FROM market.daily_bar "
        "WHERE symbol='688610.SH' ORDER BY trade_date",
    ),
    (
        "Q3 全市场某日快照",
        "SELECT symbol,close,pct_change FROM market.daily_bar WHERE trade_date='2026-10-19'",
    ),
    (
        "Q4 近20日全市场",
        "SELECT trade_date,symbol,close,volume,turnover_rate FROM market.daily_bar "
        "WHERE trade_date>='2026-09-22'",
    ),
    (
        "Q6 raw单只审计",
        "SELECT source_name,adjust_type,trade_date FROM raw.daily_bar WHERE symbol='688610.SH'",
    ),
    (
        "Q8 复权因子单只",
        "SELECT trade_date,factor FROM market.adjustment_factor "
        "WHERE symbol='688610.SH' AND adjust_type='qfq'",
    ),
]


def bench(db):
    c = duckdb.connect(db, read_only=True)
    out = {}
    for lbl, q in Q:
        ts = []
        for _ in range(3):
            t = time.perf_counter()
            c.execute(q).fetchall()
            ts.append((time.perf_counter() - t) * 1000)
        out[lbl] = statistics.median(ts)
    c.close()
    return out


A = bench(AB)
print("\n=== 步骤1: A 库（含索引）基线 ===")
for k, v in A.items():
    print(f"  {k:<24} {v:>9.2f} ms")

# 删大表索引
c = duckdb.connect(AB)
big = [
    "ix_daily_bar_quality_status",
    "ix_daily_bar_status_symbol_adjust_date",
    "ix_daily_bar_symbol_adjust_date",
    "ix_daily_bar_trade_date_type",
    "ix_adjustment_factor_date_type",
    "ix_adjustment_factor_symbol_type_date",
    "ix_raw_daily_bar_batch_id",
    "ix_raw_daily_bar_row_hash",
    "ix_raw_daily_bar_source_date",
    "ix_raw_daily_bar_source_symbol_adjust_date",
    "ix_raw_daily_bar_symbol_adjust_date",
    "ix_raw_daily_bar_symbol_date",
]
t = time.perf_counter()
ok = 0
for i in big:
    try:
        c.execute(f"DROP INDEX {i}")
        ok += 1
    except Exception as e:
        print("  FAIL", i, str(e)[:50])
c.execute("CHECKPOINT")
c.close()
print(f"\n=== 删除 {ok}/{len(big)} 个大表索引, 耗时 {time.perf_counter() - t:.1f}s ===")
ab_size_gib = os.path.getsize(AB) / 2**30
saved_gib = (os.path.getsize(SRC) - os.path.getsize(AB)) / 2**30
print(f"B 库(无大表索引): {ab_size_gib:.3f} GiB  (节省 {saved_gib:.3f} GiB)")

B = bench(AB)
print("\n=== 步骤2: B 库（无大表索引）对比 ===")
print(f"{'查询':<26}{'A 含索引':>12}{'B 无索引':>12}{'变化':>12}")
print("-" * 64)
tot = [0, 0]
for k in A:
    a, b = A[k], B[k]
    tot[0] += a
    tot[1] += b
    pct = (b - a) / a * 100 if a else 0
    print(f"{k:<26}{a:>10.2f}ms{b:>10.2f}ms{pct:>+11.1f}%")
print("-" * 64)
total_change = (tot[1] - tot[0]) / tot[0] * 100
print(f"{'合计':<26}{tot[0]:>10.2f}ms{tot[1]:>10.2f}ms{total_change:>+11.1f}%")
