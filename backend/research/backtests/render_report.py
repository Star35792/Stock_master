"""把回测结果渲染成 Markdown 报告 + 自包含 HTML 可视化。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "outputs" / "backtest"
DOC = ROOT / "aiDoc" / "data"
STAMP = "2024-10-14_2026-10-14_20d_top20"


def load():
    equity = pd.read_csv(OUT / f"equity_{STAMP}.csv", encoding="utf-8-sig", parse_dates=["trade_date"])
    equity = equity.set_index("trade_date")
    picks = pd.read_csv(OUT / f"picks_{STAMP}.csv", encoding="utf-8-sig", parse_dates=["trade_date"])
    summary = json.loads((OUT / f"summary_{STAMP}.json").read_text(encoding="utf-8"))
    period = pd.read_csv(OUT / "period_returns_20d_top20.csv", encoding="utf-8-sig")
    return equity, picks, summary, period


def yearly_table(equity: pd.DataFrame) -> list[dict]:
    rows = []
    for year, g in equity.groupby(equity.index.year):
        sr = g["total_equity"].iloc[-1] / g["total_equity"].iloc[0] - 1
        br = g["benchmark_equity"].iloc[-1] / g["benchmark_equity"].iloc[0] - 1
        dd = (g["total_equity"] / g["total_equity"].cummax() - 1).min()
        rows.append({"year": year, "strategy": sr, "benchmark": br, "excess": sr - br, "dd": dd})
    return rows


def quarterly_table(equity: pd.DataFrame) -> list[dict]:
    q = equity.resample("QE").last().pct_change().dropna()
    rows = []
    for idx, row in q.iterrows():
        rows.append(
            {
                "period": str(idx.to_period("Q")),
                "strategy": row["total_equity"],
                "benchmark": row["benchmark_equity"],
            }
        )
    return rows


def svg_equity(equity: pd.DataFrame) -> str:
    w, h = 920, 420
    pad_l, pad_r, pad_t, pad_b = 70, 110, 30, 45
    inner_w, inner_h = w - pad_l - pad_r, h - pad_t - pad_b
    dates = list(equity.index)
    strat = equity["total_equity"].astype(float).tolist()
    bench = equity["benchmark_equity"].astype(float).tolist()
    lo = min(min(strat), min(bench))
    hi = max(max(strat), max(bench))
    span = max(hi - lo, 1.0)
    lo -= span * 0.06
    hi += span * 0.06
    n = len(dates)

    def x(i: int) -> float:
        return pad_l + inner_w * i / max(n - 1, 1)

    def y(v: float) -> float:
        return pad_t + inner_h * (1 - (v - lo) / (hi - lo))

    def poly(vals: list[float]) -> str:
        return " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))

    grid_lines = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        yy = y(v)
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{pad_l + inner_w}" y2="{yy:.1f}" '
            f'stroke="var(--grid)" stroke-width="1"/>'
            f'<text x="{pad_l - 10}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="var(--muted)">{v/10000:.0f}万</text>'
        )

    ticks = []
    step = max(n // 6, 1)
    for i in range(0, n, step):
        ticks.append(
            f'<text x="{x(i):.1f}" y="{pad_t + inner_h + 22}" text-anchor="middle" '
            f'font-size="11" fill="var(--muted)">{dates[i].strftime("%Y-%m")}</text>'
        )

    last_s, last_b = strat[-1], bench[-1]
    return f"""
<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="策略与沪深300净值曲线">
  <style>
    .axis {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }}
  </style>
  <g class="axis">
    {''.join(grid_lines)}
    {''.join(ticks)}
    <polyline points="{poly(bench)}" fill="none" stroke="#8b95a5" stroke-width="2" stroke-linejoin="round" opacity="0.9"/>
    <polyline points="{poly(strat)}" fill="none" stroke="#e5484d" stroke-width="2.4" stroke-linejoin="round"/>
    <text x="{x(n-1)+8:.1f}" y="{y(last_s)+4:.1f}" font-size="12" font-weight="600" fill="#e5484d">策略 {last_s/10000:.1f}万</text>
    <text x="{x(n-1)+8:.1f}" y="{y(last_b)+4:.1f}" font-size="12" fill="#8b95a5">沪深300 {last_b/10000:.1f}万</text>
    <line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+inner_h}" stroke="var(--axis)" stroke-width="1"/>
    <line x1="{pad_l}" y1="{pad_t+inner_h}" x2="{pad_l+inner_w}" y2="{pad_t+inner_h}" stroke="var(--axis)" stroke-width="1"/>
  </g>
</svg>"""


def svg_monthly(equity: pd.DataFrame) -> str:
    m = equity.resample("ME").last().pct_change().dropna()
    vals = m["total_equity"].tolist()
    labels = [d.strftime("%y-%m") for d in m.index]
    w, h = 920, 260
    pad_l, pad_r, pad_t, pad_b = 55, 20, 25, 60
    inner_w, inner_h = w - pad_l - pad_r, h - pad_t - pad_b
    hi = max(max(vals), 0.001)
    lo = min(min(vals), -0.001)
    span = hi - lo

    def y(v: float) -> float:
        return pad_t + inner_h * (hi - v) / span

    bar_w = inner_w / len(vals) * 0.66
    parts = []
    for i, (lab, v) in enumerate(zip(labels, vals)):
        cx = pad_l + inner_w * (i + 0.5) / len(vals)
        top = y(max(v, 0))
        bottom = y(min(v, 0))
        color = "#e5484d" if v >= 0 else "#2f9e44"
        parts.append(
            f'<rect x="{cx - bar_w/2:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
            f'height="{max(bottom-top,1):.1f}" fill="{color}" opacity="0.85"/>'
        )
        if i % 3 == 0:
            parts.append(
                f'<text x="{cx:.1f}" y="{pad_t+inner_h+18}" text-anchor="middle" '
                f'font-size="10" fill="var(--muted)">{lab}</text>'
            )
    zero = y(0)
    return f"""
<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="月度收益分布">
  <style>.axis2 {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; }}</style>
  <g class="axis2">
    {''.join(parts)}
    <line x1="{pad_l}" y1="{zero:.1f}" x2="{pad_l+inner_w}" y2="{zero:.1f}" stroke="var(--axis)" stroke-width="1"/>
    <text x="{pad_l-8}" y="{y(hi)+4:.1f}" text-anchor="end" font-size="10" fill="var(--muted)">{hi:+.0%}</text>
    <text x="{pad_l-8}" y="{y(lo)+4:.1f}" text-anchor="end" font-size="10" fill="var(--muted)">{lo:+.0%}</text>
  </g>
</svg>"""


SENS = [
    ("10 日 / Top20", 0.6328, 0.2906, -0.2043),
    ("20 日 / Top20（基准配置）", 0.2475, 0.1220, -0.1955),
    ("40 日 / Top20", 0.1234, 0.0624, -0.1583),
    ("20 日 / Top10", 0.2020, 0.1005, -0.2687),
    ("20 日 / Top30", 0.2629, 0.1291, -0.1920),
]


def main() -> None:
    equity, picks, summary, period = load()
    met = summary["metrics"]
    years = yearly_table(equity)
    quarters = quarterly_table(equity)

    last_signal = picks["trade_date"].max()
    holdings = picks[picks["trade_date"] == last_signal].sort_values("rank")

    # ---------- Markdown ----------
    md = []
    md.append("# 低价超跌反弹策略 · 历史回测报告")
    md.append("")
    md.append("**回测区间**：2024-10-14 ~ 2026-10-14（464 个交易日，24 个调仓期）  ")
    md.append("**生成时间**：2026-10-19  ")
    md.append("**数据源**：`backend/data/stock_master.duckdb`（`market.daily_bar` 前复权 + `market.security`）  ")
    md.append("**回测引擎**：`app.backtest.DailyEventBacktestEngine`（信号日次日开盘成交、整手交易、A 股费用模型）")
    md.append("")
    md.append("## 一、回测设置")
    md.append("")
    md.append("| 项目 | 设置 |")
    md.append("|---|---|")
    md.append("| 选股条件 | 价格 ≤15 元；20 日跌幅 -35%~-15%；5 日涨跌幅 -5%~+15%；20 日均额 ≥1000 万元 |")
    md.append("| 排除项 | 金融/银行/证券/保险/期货/黄金/贵金属行业；ST/*ST |")
    md.append("| 评分 | 价格、20 日跌幅、5 日涨幅、成交额 四项 percentile rank 等权 |")
    md.append("| 持仓 | Top 20 等权，每只 5% |")
    md.append("| 调仓 | 每 20 个交易日，信号日次日开盘价成交 |")
    md.append("| 初始资金 | 100 万元 |")
    md.append("| 费用 | 佣金万 3（最低 5 元）+ 印花税千 0.5（卖出）+ 过户费 + 交易所费 + 滑点 2bp |")
    md.append("| 基准 | 沪深 300（000300.SH） |")
    md.append("")
    md.append("## 二、核心绩效")
    md.append("")
    md.append("| 指标 | 策略 | 沪深 300 |")
    md.append("|---|---:|---:|")
    md.append(f"| 累计收益 | **{met['total_return']:+.2%}** | {met['benchmark_total_return']:+.2%} |")
    md.append(f"| 年化收益 | **{met['annualized_return']:+.2%}** | {(1+met['benchmark_total_return'])**(1/met['years'])-1:+.2%} |")
    md.append(f"| 超额收益 | **{met['excess_return']:+.2%}** | — |")
    md.append(f"| 最大回撤 | {met['max_drawdown']:.2%} | — |")
    md.append(f"| 夏普比率 | {met['sharpe']:.3f} | — |")
    md.append(f"| Calmar | {met['calmar']:.3f} | — |")
    md.append(f"| 日胜率 | {met['win_rate_daily']:.1%} | — |")
    md.append(f"| 年换手 | {met['annual_turnover']:.1f} 倍 | — |")
    md.append(f"| 交易笔数 | {summary['trade_count']} 笔 | — |")
    md.append("")
    md.append("**期度统计（24 个调仓期）**：期胜率 "
              f"{(period['strategy']>0).mean():.1%}（{(period['strategy']>0).sum()}/{len(period)}），"
              f"跑赢基准 {(period['excess']>0).mean():.1%}（{(period['excess']>0).sum()}/{len(period)}），"
              f"平均期收益 {period['strategy'].mean():+.2%}，最好 {period['strategy'].max():+.2%}，最差 {period['strategy'].min():+.2%}。")
    md.append("")
    md.append("## 三、分年度表现")
    md.append("")
    md.append("| 年度 | 策略 | 沪深 300 | 超额 | 年内最大回撤 |")
    md.append("|---|---:|---:|---:|---:|")
    for row in years:
        md.append(f"| {row['year']} | {row['strategy']:+.2%} | {row['benchmark']:+.2%} | {row['excess']:+.2%} | {row['dd']:.2%} |")
    md.append("")
    md.append("> 2024 年只含 10-11 月至年底的区间，非完整年度。")
    md.append("")
    md.append("## 四、分季度表现")
    md.append("")
    md.append("| 季度 | 策略 | 沪深 300 |")
    md.append("|---|---:|---:|")
    for row in quarters:
        md.append(f"| {row['period']} | {row['strategy']:+.2%} | {row['benchmark']:+.2%} |")
    md.append("")
    md.append("## 五、参数敏感性")
    md.append("")
    md.append("| 配置 | 累计收益 | 年化 | 最大回撤 |")
    md.append("|---|---:|---:|---:|")
    for name, total, ann, dd in SENS:
        md.append(f"| {name} | {total:+.2%} | {ann:+.2%} | {dd:.2%} |")
    md.append("")
    md.append("## 六、最新一期持仓（信号日 2026-10-09，10-14 为回测终点）")
    md.append("")
    md.append("| 排名 | 代码 | 名称 | 行业 | 价格 | 5日 | 20日 | 评分 |")
    md.append("|---:|---|---|---|---:|---:|---:|---:|")
    for _, r in holdings.iterrows():
        md.append(
            f"| {int(r['rank'])} | {r['symbol']} | {r['name']} | {r['industry'] or '—'} | "
            f"{r['p_latest']:.2f} | {r['ret5']*100:+.2f}% | {r['ret20']*100:+.2f}% | {r['score']:.3f} |"
        )
    md.append("")
    md.append("## 七、关键结论")
    md.append("")
    md.append("1. **策略长期有效但收益高度集中**：2 年累计 +24.75%、年化 12.20%，跑赢沪深 300 约 22.7 个百分点；但 2025 年单年 +44.73% 贡献了几乎全部收益，2024Q4 与 2026 年基本走平或小亏。样本外稳健性尚不能确认。")
    md.append("2. **信号衰减极快**：10 日调仓年化 29.1%，20 日降到 12.2%，40 日只剩 6.2%。策略本质是短期反转，持仓周期越长、alpha 流失越多。")
    md.append("3. **逆向属性明显，可作分散化工具**：2026Q2 沪深 300 涨 11.9% 时策略跌 10.1%，2026Q3 沪深 300 跌 12.5% 时策略涨 15.8%，与大盘相关性低甚至为负。")
    md.append("4. **回撤不小**：最大回撤 -19.55%，单期最差 -14.32%（2024-12~2025-01），需要能承受接近 20% 的净值回撤。")
    md.append("5. **成本敏感**：年换手约 10 倍，佣金、印花税、滑点合计对年化收益的拖累约 1 个百分点量级；若按更高滑点或冲击成本，收益会进一步下降。")
    md.append("")
    md.append("## 八、数据局限（结论需打折看待）")
    md.append("")
    md.append("| 问题 | 实际情况 | 对回测的影响 |")
    md.append("|---|---|---|")
    md.append("| 涨跌停价缺失 | `limit_up`/`limit_down` 在 262 万条个股 bar 中**全部为空** | 无法模拟“开盘涨停买不进”，超跌反弹股次日高开甚至涨停的概率不低，**实际收益会低于回测** |")
    md.append("| 停牌标记缺失 | `is_suspended` 全部为 False | 停牌期间仍按可交易处理，略微高估 |")
    md.append("| 幸存者偏差 | `market.security` 5582 只股票全部 `is_active=True`，无退市股 | 已退市/长期停牌股不在候选池，**高估收益** |")
    md.append("| 历史 ST 状态缺失 | 262 万条历史 bar 中 `special_treatment_status` 非空仅 197 条（0.01%） | 只能用“当前名称含 ST”过滤，历史上曾 ST、现已摘帽的股票会被误纳入 |")
    md.append("| 前复权价格用于阈值 | 15 元价格线用 qfq 价判断 | 历史日期的最低价判断与真实价格有偏差，近年偏差小、越早越大 |")
    md.append("")
    md.append("综合看，**真实可实现的收益大概率低于回测值**，主要漏损来自涨停不可买与幸存者偏差。建议把 +24.75% / 年化 12.2% 视为乐观上限。")
    md.append("")
    md.append("## 九、复现方式")
    md.append("")
    md.append("```bash")
    md.append("python backend/research/backtests/low_price_rebound_backtest.py \\")
    md.append("    --start 2024-10-14 --end 2026-10-14 \\")
    md.append("    --rebalance-days 20 --top-n 20")
    md.append("```")
    md.append("")
    md.append("输出目录：`outputs/backtest/`（equity / picks / trades / summary 四类文件）")
    md.append("")
    md.append("---")
    md.append("")
    md.append("> 本报告由历史数据回测生成，不构成任何投资建议。回测收益不代表未来表现，且受上述数据局限影响存在高估。")
    (DOC / "20261019-low-price-rebound-backtest.md").write_text("\n".join(md), encoding="utf-8")

    # ---------- HTML ----------
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<title>低价超跌反弹策略回测 · 2024-10 ~ 2026-10</title>
<style>
  :root {{
    --bg: #ffffff; --panel: #fbfbfd; --text: #1c2024; --muted: #6b7280;
    --grid: #eceef1; --axis: #c9ced6; --border: #e5e7eb;
    --up: #e5484d; --down: #2f9e44;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #14161a; --panel: #1b1e24; --text: #e6e8ec; --muted: #9aa3af;
      --grid: #262a31; --axis: #3a4048; --border: #2a2f36;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 32px 24px 64px; background: var(--bg); color: var(--text);
    font-family: -apple-system, "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    line-height: 1.6;
  }}
  .wrap {{ max-width: 980px; margin: 0 auto; }}
  h1 {{ font-size: 26px; margin: 0 0 6px; letter-spacing: -0.3px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 28px; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 28px; }}
  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
  .card .k {{ font-size: 12px; color: var(--muted); }}
  .card .v {{ font-size: 22px; font-weight: 650; margin-top: 2px; font-variant-numeric: tabular-nums; }}
  .card .v.up {{ color: var(--up); }}
  .card .v.down {{ color: var(--down); }}
  .section {{ margin: 30px 0 8px; font-size: 16px; font-weight: 650; }}
  .chart {{ background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 8px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }}
  th, td {{ padding: 8px 10px; border-bottom: 1px solid var(--border); text-align: right; }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 500; font-size: 12px; }}
  .up {{ color: var(--up); }}
  .down {{ color: var(--down); }}
  .note {{ background: var(--panel); border-left: 3px solid #d97706; border-radius: 6px; padding: 12px 16px; font-size: 13px; color: var(--muted); margin-top: 10px; }}
  footer {{ margin-top: 40px; color: var(--muted); font-size: 12px; border-top: 1px solid var(--border); padding-top: 14px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>低价超跌反弹策略 · 历史回测</h1>
  <div class="sub">2024-10-14 ~ 2026-10-14 · 24 个调仓期 · 每 20 交易日调仓 · Top20 等权 · 初始 100 万</div>

  <div class="cards">
    <div class="card"><div class="k">累计收益</div><div class="v up">{met['total_return']:+.2%}</div></div>
    <div class="card"><div class="k">年化收益</div><div class="v up">{met['annualized_return']:+.2%}</div></div>
    <div class="card"><div class="k">超额（vs 沪深300）</div><div class="v up">{met['excess_return']:+.2%}</div></div>
    <div class="card"><div class="k">最大回撤</div><div class="v down">{met['max_drawdown']:.2%}</div></div>
    <div class="card"><div class="k">夏普比率</div><div class="v">{met['sharpe']:.2f}</div></div>
  </div>

  <div class="section">净值曲线</div>
  <div class="chart">{svg_equity(equity)}</div>

  <div class="section">月度收益分布</div>
  <div class="chart">{svg_monthly(equity)}</div>

  <div class="section">分年度表现</div>
  <table>
    <thead><tr><th>年度</th><th>策略</th><th>沪深 300</th><th>超额</th><th>年内最大回撤</th></tr></thead>
    <tbody>
    {''.join(f'<tr><td>{r["year"]}</td><td class="{"up" if r["strategy"]>=0 else "down"}">{r["strategy"]:+.2%}</td><td>{r["benchmark"]:+.2%}</td><td class="{"up" if r["excess"]>=0 else "down"}">{r["excess"]:+.2%}</td><td class="down">{r["dd"]:.2%}</td></tr>' for r in years)}
    </tbody>
  </table>

  <div class="section">参数敏感性</div>
  <table>
    <thead><tr><th>配置</th><th>累计收益</th><th>年化</th><th>最大回撤</th></tr></thead>
    <tbody>
    {''.join(f'<tr><td>{n}</td><td class="{"up" if t>0 else "down"}">{t:+.2%}</td><td>{a:+.2%}</td><td class="down">{d:.2%}</td></tr>' for n,t,a,d in SENS)}
    </tbody>
  </table>

  <div class="note">
    <strong>重要局限：</strong>涨跌停与停牌字段全空、候选池无退市股（幸存者偏差）、历史 ST 状态覆盖仅 0.01%。
    因此真实可实现收益大概率低于回测值，建议把年化 12.2% 视为乐观上限。
  </div>

  <div class="section">最新一期持仓（信号日 2026-10-09）</div>
  <table>
    <thead><tr><th>#</th><th>代码</th><th>名称</th><th>行业</th><th>价格</th><th>5日</th><th>20日</th><th>评分</th></tr></thead>
    <tbody>
    {''.join(f'<tr><td>{int(r["rank"])}</td><td>{r["symbol"]}</td><td>{r["name"]}</td><td>{r["industry"] or "—"}</td><td>{r["p_latest"]:.2f}</td><td class="{"up" if r["ret5"]>=0 else "down"}">{r["ret5"]*100:+.2f}%</td><td class="down">{r["ret20"]*100:+.2f}%</td><td>{r["score"]:.3f}</td></tr>' for _, r in holdings.iterrows())}
    </tbody>
  </table>

  <footer>
    回测引擎：DailyEventBacktestEngine（信号日次日开盘成交、整手、A 股费用模型）<br/>
    数据源：backend/data/stock_master.duckdb · 生成时间 2026-10-19<br/>
    本页面由历史数据回测生成，不构成投资建议。
  </footer>
</div>
</body>
</html>"""
    (DOC / "low_price_rebound_backtest_20261019.html").write_text(html, encoding="utf-8")
    print("saved md + html")


if __name__ == "__main__":
    main()
