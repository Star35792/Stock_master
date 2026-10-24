import duckdb, json, sys
src, out = sys.argv[1], sys.argv[2]
con = duckdb.connect(src, read_only=True)
res = {}
tabs = con.execute("SELECT schema_name, table_name FROM duckdb_tables() ORDER BY 1,2").fetchall()
for s,t in tabs:
    res[f"cnt::{s}.{t}"] = con.execute(f'SELECT count(*) FROM "{s}"."{t}"').fetchone()[0]
probes = {
 "market.daily_bar": "SELECT sum(volume), sum(amount), sum(close), sum(open), sum(high), sum(low), sum(pct_change), sum(turnover_rate), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM market.daily_bar",
 "market.adjustment_factor": "SELECT sum(qfq_factor), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM market.adjustment_factor",
 "raw.daily_bar": "SELECT sum(volume), sum(amount), sum(close), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM raw.daily_bar",
 "factor.factor_value": "SELECT count(*), sum(value), min(trade_date), max(trade_date), count(DISTINCT symbol) FROM factor.factor_value",
 "fundamental.financial_indicator": "SELECT sum(revenue), sum(net_profit), count(*) FROM fundamental.financial_indicator",
 "market.valuation_snapshot": "SELECT sum(pe_ttm), sum(pb), sum(total_mv), count(*) FROM market.valuation_snapshot",
 "market.corporate_action": "SELECT count(*), min(ex_date), max(ex_date) FROM market.corporate_action",
}
for tbl, sql in probes.items():
    try:
        res[f"agg::{tbl}"] = [str(x) for x in con.execute(sql).fetchone()]
    except Exception as e:
        res[f"agg::{tbl}"] = f"SKIP:{str(e)[:80]}"
json.dump(res, open(out,"w"), ensure_ascii=False, indent=1)
print("keys:", len(res), "->", out)
con.close()
