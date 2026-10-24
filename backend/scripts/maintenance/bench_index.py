import duckdb, time, json, statistics
con = duckdb.connect("data/stock_master.duckdb", read_only=True)

# 真实业务查询模式（取自因子/选股/回测实际用法）
QUERIES = [
 ("Q1 单只股票完整K线(选股详情页)", "SELECT trade_date,open,high,low,close,volume,pct_change FROM market.daily_bar WHERE symbol='688610.SH' ORDER BY trade_date"),
 ("Q2 单只股票近期窗口", "SELECT trade_date,close,turnover_rate FROM market.daily_bar WHERE symbol='688610.SH' AND trade_date>='2026-01-01'"),
 ("Q3 全市场某日快照(因子横截面)", "SELECT symbol,close,pct_change,turnover_rate FROM market.daily_bar WHERE trade_date='2026-10-19'"),
 ("Q4 近20日全市场(因子计算主查询)", "SELECT trade_date,symbol,close,volume,turnover_rate FROM market.daily_bar WHERE trade_date>='2026-09-22'"),
 ("Q5 质量过滤(选股前置)", "SELECT count(*) FROM market.daily_bar WHERE quality_status='ok' AND trade_date>='2026-10-01'"),
 ("Q6 raw 单只股票落地审计", "SELECT source_name,adjust_type,trade_date,close FROM raw.daily_bar WHERE symbol='688610.SH'"),
 ("Q7 raw 按source核对", "SELECT count(*) FROM raw.daily_bar WHERE source_name='hithink_finance_dump' AND trade_date='2026-10-19'"),
 ("Q8 复权因子单只", "SELECT trade_date,factor FROM market.adjustment_factor WHERE symbol='688610.SH' AND adjust_type='qfq' ORDER BY trade_date"),
 ("Q9 因子值按日期取(API分页)", "SELECT factor_name,symbol,value FROM factor.factor_value WHERE trade_date='2026-10-19' LIMIT 100"),
 ("Q10 行业/日期范围聚合", "SELECT symbol,count(*) FROM market.daily_bar WHERE trade_date BETWEEN '2026-09-01' AND '2026-10-19' GROUP BY symbol LIMIT 50"),
]
res=[]
for lbl, q in QUERIES:
    plan = con.execute("EXPLAIN "+q).fetchone()[1]
    uses_idx = ("INDEX_SCAN" in plan) or ("ART" in plan) or ("INDEX" in plan)
    ts=[]
    for _ in range(3):
        t=time.perf_counter(); con.execute(q).fetchall(); ts.append((time.perf_counter()-t)*1000)
    res.append({"label":lbl,"sql":q,"uses_index":uses_idx,"ms":round(statistics.median(ts),2)})
    print(f"{lbl:<38} {'用索引' if uses_idx else '全表扫描':<8} {statistics.median(ts):>9.2f} ms")
json.dump(res, open("scripts/maintenance/index_bench.json","w"), ensure_ascii=False, indent=1)
print(f"\n使用索引的查询: {sum(r['uses_index'] for r in res)}/{len(res)}")
print(f"总耗时: {sum(r['ms'] for r in res):.1f} ms")
con.close()
