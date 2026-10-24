import duckdb, json, sys
before = json.load(open('scripts/maintenance/deep_before.json'))
con = duckdb.connect("data/stock_master.rebuild.tmp.duckdb", read_only=True)
after = {}
tabs = con.execute("SELECT schema_name, table_name FROM duckdb_tables() ORDER BY 1,2").fetchall()
for s,t in tabs:
    after[f"cnt::{s}.{t}"] = con.execute(f'SELECT count(*) FROM "{s}"."{t}"').fetchone()[0]
probes = {
 "market.daily_bar": "SELECT sum(volume), sum(amount), sum(close), sum(open), sum(high), sum(low), sum(pct_change), sum(turnover_rate), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM market.daily_bar",
 "market.adjustment_factor": "SELECT sum(factor), min(trade_date), max(trade_date), count(DISTINCT symbol), count(*), sum(release_id IS NOT NULL::INT) FROM market.adjustment_factor",
 "raw.daily_bar": "SELECT sum(volume), sum(amount), sum(close), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM raw.daily_bar",
 "factor.factor_value": "SELECT count(*), sum(value), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM factor.factor_value",
 "fundamental.financial_indicator": "SELECT sum(value), count(*), count(DISTINCT symbol), count(DISTINCT indicator_name) FROM fundamental.financial_indicator",
 "market.valuation_snapshot": "SELECT sum(pe_ttm), sum(pb), sum(total_market_cap), sum(float_market_cap), count(*) FROM market.valuation_snapshot",
 "market.corporate_action": "SELECT count(*), min(ex_date), max(ex_date) FROM market.corporate_action",
}
for tbl, sql in probes.items():
    try: after[f"agg::{tbl}"] = [str(x) for x in con.execute(sql).fetchone()]
    except Exception as e: after[f"agg::{tbl}"] = f"SKIP:{str(e)[:90]}"

fail = []
for k in before:
    b, a = before[k], after.get(k, "MISSING")
    if b != a:
        fail.append((k, b, a))
print("=" * 66)
print("逐表行数比对:", len([k for k in before if k.startswith('cnt::')]), "张表")
print("数值指纹比对:", len([k for k in before if k.startswith('agg::')]), "张表")
if not fail:
    print("结果: 全部一致 ✓  零差异")
else:
    print(f"结果: 发现 {len(fail)} 处差异 ✗")
    for k, b, a in fail[:25]:
        bs = b if isinstance(b,str) else [str(x)[:18] for x in b]
        as_ = a if isinstance(a,str) else [str(x)[:18] for x in a]
        print(f"  {k}\n    before={bs}\n    after ={as_}")
nt = con.execute("SELECT count(*) FROM duckdb_tables()").fetchone()[0]
ns = con.execute("SELECT count(DISTINCT schema_name) FROM duckdb_tables()").fetchone()[0]
print(f"新库: {nt} 张表 / {ns} 个 schema (原库 45 / 10)")
con.close()
sys.exit(1 if fail else 0)
