import json

import duckdb

DB = "data/stock_master.duckdb"
con = duckdb.connect(DB, read_only=True)
bs = con.execute("SELECT block_size FROM pragma_database_size()").fetchone()[0]

idx = con.execute(
    "SELECT index_name, table_name, schema_name, is_unique, sql FROM duckdb_indexes()"
).fetchall()
rows = []
for nm, tbl, sch, uniq, sql in idx:
    full = f"{sch}.{tbl}"
    try:
        n = con.execute(f'SELECT count(*) FROM "{sch}"."{tbl}"').fetchone()[0]
    except Exception:
        n = 0
    rows.append({"index": nm, "table": full, "table_rows": n, "unique": uniq, "sql": sql})
json.dump(rows, open("scripts/maintenance/index_inventory.json", "w"), ensure_ascii=False, indent=1)

# 大表索引清单
print("=== 大表（>100万行）上的索引 ===")
big = [r for r in rows if r["table_rows"] > 1_000_000]
for r in sorted(big, key=lambda x: -x["table_rows"]):
    print(f"  {r['index']:<48} {r['table']:<28} {r['table_rows']:>12,} 行")
print(f"\n大表索引数: {len(big)} / 总索引数 {len(rows)}")
print(f"涉及总行数: {sum(r['table_rows'] for r in big):,}")
con.close()
