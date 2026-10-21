"""低价超跌反弹策略 —— 事件驱动历史回测。

策略口径与 backend/research/screens/low_price_oversold_rebound.sql 保持一致：
  * 价格 <= 15 元（前复权）
  * 20 日涨跌幅 in [-35%, -15%]
  * 5 日涨跌幅 in [-5%, +15%]
  * 20 日均成交额 >= 1000 万元
  * 排除 ST / *ST
  * 4 因子 percentile rank 等权打分，取 Top N 等权持有
    --rank-mode intent  ：低价↑ / 20 日跌幅深↑ / 5 日走势强↑ / 20 日均额↑（策略字面本意）
    --rank-mode inverse ：低价区间内偏高↑ / 浅跌↑ / 5 日偏弱↑ / 20 日均额↑（历史实现）
  * 默认排除金融/银行/证券/保险/期货/黄金/贵金属；--include-financials 关闭排除

回测引擎复用 app.backtest.DailyEventBacktestEngine（信号日次日开盘成交、整手、A 股费用模型）。

可选显著性检验：``--compare-equity <对照净值.csv>`` 时用配对 block bootstrap
（对齐两条日净值曲线、日对数收益差分块重采样）检验本次 run 与对照 run 的
收益差是否显著，结果写入 summary JSON 的 ``significance_test`` 字段。
块长默认取调仓周期（持仓在周期内高度自相关），可用 ``--block-days`` 覆盖。

用法：
    python backend/research/backtests/low_price_rebound_backtest.py \
        --start 2024-10-14 --end 2026-10-14 --rebalance-days 20 --top-n 20 \
        --rank-mode intent --compare-equity outputs/backtest/equity_..._inverse.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.backtest.engine import (  # noqa: E402
    DailyEventBacktestEngine,
    EquitySnapshot,
    TargetWeightSignal,
)
from app.backtest.fees import A_SHARE_COST_V1, build_fee_model  # noqa: E402
from app.repositories.warm_layer import WarmLayerStore  # noqa: E402

DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"
WARM_STORE = WarmLayerStore()

EXCLUDED_INDUSTRY_KEYWORDS = (
    "金融",
    "银行",
    "证券",
    "保险",
    "期货",
    "黄金",
    "贵金属",
)
EXCLUDED_NAME_KEYWORDS = (
    "银行",
    "保险",
    "证券",
    "信托",
    "期货",
    "黄金",
)


@dataclass(slots=True)
class Bar:
    symbol: str
    trade_date: date
    open: Decimal
    close: Decimal
    is_suspended: bool
    limit_up: Decimal | None
    limit_down: Decimal | None


def _release_tag(params: object) -> str | None:
    if isinstance(params, dict):
        return params.get("release_tag")
    if isinstance(params, str):
        try:
            parsed = json.loads(params)
        except ValueError:
            return None
        return parsed.get("release_tag") if isinstance(parsed, dict) else None
    return None


def fetch_data_fingerprint(con: duckdb.DuckDBPyConnection) -> dict[str, object]:
    """记录回测所用的数据快照指纹，绑定到 summary，保证结论可复现、可比对。

    历史教训：本库的历史 bar 会被按质量分重选改写，没有快照指纹的回测结论
    无法复现（同一配置曾从 +24.75% 漂到 +11.50%）。
    """
    fp: dict[str, object] = {}
    # market.daily_bar in the warm layer only holds adjust_type='none' rows;
    # forward-adjusted bars are materialized in market.prices_qfq.
    daily_none = con.execute(
        """
        SELECT count(*), max(trade_date)
        FROM market.daily_bar WHERE adjust_type = 'none'
        """
    ).fetchone()
    prices_qfq = con.execute(
        """
        SELECT count(*), max(trade_date) FROM market.prices_qfq
        """
    ).fetchone()
    fp["daily_bar"] = {
        "none": {"rows": int(daily_none[0]), "max_trade_date": str(daily_none[1])},
        "qfq": {"rows": int(prices_qfq[0]), "max_trade_date": str(prices_qfq[1])},
    }
    fp["corporate_action"] = {
        "rows": con.execute("SELECT count(*) FROM market.corporate_action").fetchone()[0],
        "max_ex_date": str(
            con.execute("SELECT max(ex_date) FROM market.corporate_action").fetchone()[0]
        ),
    }
    fp["adjustment_factor_release"] = con.execute(
        """
        SELECT release_id FROM market.adjustment_factor
        WHERE release_id IS NOT NULL
        GROUP BY release_id ORDER BY count(*) DESC LIMIT 1
        """
    ).fetchone()[0]
    fp["latest_import_batches"] = [
        {"batch_no": r[0], "dataset": r[1], "status": r[2], "release_tag": _release_tag(r[3])}
        for r in con.execute(
            """
            SELECT batch_no, dataset, status, request_params
            FROM raw.import_batch ORDER BY id DESC LIMIT 3
            """
        ).fetchall()
    ]
    return fp


def load_trading_days(con: duckdb.DuckDBPyConnection, start: date, end: date) -> list[date]:
    rows = con.execute(
        """
        SELECT DISTINCT trade_date
        FROM market.prices_qfq
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [start, end],
    ).fetchall()
    return [r[0] for r in rows]


def build_rebalance_dates(trading_days: list[date], every: int) -> list[date]:
    """从区间首个满足窗口条件的日期开始，每 every 个交易日取一个信号日。"""
    # 需要 20 日窗口，前 20 个交易日只用于热身，不产生信号
    candidates = trading_days[20:]
    return candidates[::every]


def compute_signals(
    con: duckdb.DuckDBPyConnection,
    signal_dates: list[date],
    warmup_start: date,
    *,
    max_price: Decimal,
    min_ret20: Decimal,
    max_ret20: Decimal,
    min_ret5: Decimal,
    max_ret5: Decimal,
    min_avg_amount: Decimal,
    top_n: int,
    rank_mode: str = "inverse",
    include_financials: bool = False,
) -> tuple[pd.DataFrame, dict[date, list[str]]]:
    """在每个信号日按策略口径选出 Top N，返回选股明细与 {信号日: [symbol]}。

    rank_mode 与选股 SQL 的同名口径保持一致：
      intent   低价 / 20 日跌幅深 / 5 日走势强 / 流动性
      inverse  低价区间内偏高 / 浅跌 / 5 日偏弱 / 流动性（历史实现，样本内表现更好）
    """
    if not signal_dates:
        return pd.DataFrame(), {}

    placeholders = ",".join("?" for _ in signal_dates)

    sql = f"""
        WITH stock_bars AS (
            SELECT
                p.symbol,
                p.trade_date,
                p.close,
                p.amount,
                d.special_treatment_status
            FROM market.prices_qfq p
            LEFT JOIN market.daily_bar d
                   ON d.symbol = p.symbol
                  AND d.trade_date = p.trade_date
                  AND d.adjust_type = 'none'
            WHERE p.security_type = 'stock'
              AND p.trade_date >= ?
              AND p.trade_date <= ?
        ),
        windowed AS (
            SELECT
                symbol,
                trade_date,
                close,
                amount,
                special_treatment_status,
                LAG(close, 20) OVER w AS close_20,
                LAG(close, 5) OVER w AS close_5,
                AVG(amount) OVER (
                    PARTITION BY symbol ORDER BY trade_date
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ) AS avg_amount_20
            FROM stock_bars
            WINDOW w AS (PARTITION BY symbol ORDER BY trade_date)
        )
        SELECT
            w.symbol,
            s.name,
            s.industry,
            w.trade_date,
            w.special_treatment_status,
            w.close AS p_latest,
            w.close / w.close_5 - 1 AS ret5,
            w.close / w.close_20 - 1 AS ret20,
            w.avg_amount_20
        FROM windowed w
        JOIN market.security s ON s.symbol = w.symbol
        WHERE w.trade_date IN ({placeholders})
          AND s.security_type = 'stock'
          AND s.is_active
          AND w.close_20 IS NOT NULL
          AND w.close_5 IS NOT NULL
          AND w.close <= ?
          AND (w.close / w.close_20 - 1) BETWEEN ? AND ?
          AND (w.close / w.close_5 - 1) BETWEEN ? AND ?
          AND w.avg_amount_20 >= ?
        ORDER BY w.trade_date, w.symbol
    """
    params: list[object] = [warmup_start, signal_dates[-1], *signal_dates]
    params += [max_price, min_ret20, max_ret20, min_ret5, max_ret5, min_avg_amount]
    frame = con.execute(sql, params).fetchdf()

    if frame.empty:
        return frame, {}

    # ST 过滤始终生效；金融类排除按开关决定
    st_ok = ~frame["name"].fillna("").str.contains("ST", regex=False)
    st_flag_ok = frame["special_treatment_status"].fillna("") != "ST"
    frame = frame[st_ok & st_flag_ok].copy()

    if not include_financials:
        industry_ok = ~frame["industry"].fillna("").str.contains(
            "|".join(EXCLUDED_INDUSTRY_KEYWORDS), regex=True
        )
        name_ok = ~frame["name"].fillna("").str.contains(
            "|".join(EXCLUDED_NAME_KEYWORDS), regex=True
        )
        frame = frame[industry_ok & name_ok].copy()

    if frame.empty:
        return frame, {}

    # 4 因子 percentile rank 等权打分（与选股 SQL 同口径）
    rk_low_price = frame.groupby("trade_date")["p_latest"].rank(
        pct=True, ascending=False
    )
    rk_deep_drop = frame.groupby("trade_date")["ret20"].rank(pct=True, ascending=False)
    rk_ret5_up = frame.groupby("trade_date")["ret5"].rank(pct=True)
    rk_liquid = frame.groupby("trade_date")["avg_amount_20"].rank(pct=True)
    if rank_mode == "intent":
        frame["score"] = (
            rk_low_price + rk_deep_drop + rk_ret5_up + rk_liquid
        ) / 4
    else:
        frame["score"] = (
            (1 - rk_low_price) + (1 - rk_deep_drop) + (1 - rk_ret5_up) + rk_liquid
        ) / 4

    frame = frame.sort_values(["trade_date", "score"], ascending=[True, False])
    picks = frame.groupby("trade_date").head(top_n).copy()
    picks["rank"] = picks.groupby("trade_date").cumcount() + 1

    selection: dict[date, list[str]] = {}
    for signal_date, group in picks.groupby("trade_date"):
        selection[pd.Timestamp(signal_date).date()] = group["symbol"].tolist()
    return picks, selection


def load_bars(
    con: duckdb.DuckDBPyConnection,
    symbols: list[str],
    start: date,
    end: date,
) -> list[Bar]:
    if not symbols:
        return []
    placeholders = ",".join("?" for _ in symbols)
    rows = con.execute(
        f"""
        SELECT
            p.symbol,
            p.trade_date,
            p.open,
            p.close,
            d.is_suspended,
            d.limit_up,
            d.limit_down
        FROM market.prices_qfq p
        LEFT JOIN market.daily_bar d
               ON d.symbol = p.symbol
              AND d.trade_date = p.trade_date
              AND d.adjust_type = 'none'
        WHERE p.trade_date BETWEEN ? AND ?
          AND p.symbol IN ({placeholders})
        ORDER BY p.trade_date, p.symbol
        """,
        [start, end, *symbols],
    ).fetchall()

    bars: list[Bar] = []
    for symbol, trade_date, open_, close, suspended, limit_up, limit_down in rows:
        if open_ is None or close is None:
            continue
        bars.append(
            Bar(
                symbol=symbol,
                trade_date=trade_date,
                open=Decimal(str(open_)),
                close=Decimal(str(close)),
                is_suspended=bool(suspended) if suspended is not None else False,
                limit_up=Decimal(str(limit_up)) if limit_up is not None else None,
                limit_down=Decimal(str(limit_down)) if limit_down is not None else None,
            )
        )
    return bars


def build_execution_calendar(
    con: duckdb.DuckDBPyConnection, symbols: list[str], start: date, end: date
) -> dict[str, list[date]]:
    if not symbols:
        return {}
    placeholders = ",".join("?" for _ in symbols)
    rows = con.execute(
        f"""
        SELECT symbol, trade_date
        FROM market.prices_qfq
        WHERE trade_date BETWEEN ? AND ?
          AND symbol IN ({placeholders})
        ORDER BY symbol, trade_date
        """,
        [start, end, *symbols],
    ).fetchall()
    calendar: dict[str, list[date]] = {}
    for symbol, trade_date in rows:
        calendar.setdefault(symbol, []).append(trade_date)
    return calendar


def build_signals(
    selection: dict[date, list[str]], top_n: int, weight: Decimal
) -> list[TargetWeightSignal]:
    """本期入选股票给 weight；上期持有但本期落选的股票显式给 0 权重以强制卖出。"""
    signals: list[TargetWeightSignal] = []
    prev_holdings: set[str] = set()

    for signal_date in sorted(selection):
        picks = selection[signal_date]
        current = set(picks)
        for symbol in picks:
            signals.append(
                TargetWeightSignal(
                    signal_date=signal_date,
                    symbol=symbol,
                    target_weight=weight,
                )
            )
        for symbol in sorted(prev_holdings - current):
            signals.append(
                TargetWeightSignal(
                    signal_date=signal_date,
                    symbol=symbol,
                    target_weight=Decimal(),
                )
            )
        prev_holdings = current
    return signals


def compute_metrics(
    equity_curve: list[EquitySnapshot], trades, initial_cash: Decimal
) -> dict[str, float]:
    if not equity_curve:
        return {}
    series = pd.Series(
        [float(point.total_equity) for point in equity_curve],
        index=pd.to_datetime([point.trade_date for point in equity_curve]),
    )
    returns = series.pct_change().dropna()
    total_return = series.iloc[-1] / float(initial_cash) - 1
    days = (series.index[-1] - series.index[0]).days
    years = max(days / 365.25, 1e-9)
    annualized = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0
    running_max = series.cummax()
    drawdown = series / running_max - 1
    max_drawdown = float(drawdown.min())

    risk_free_daily = (1 + 0.02) ** (1 / 252) - 1
    excess = returns - risk_free_daily
    sharpe = (
        float(excess.mean() / excess.std() * (252 ** 0.5))
        if len(excess) > 1 and excess.std() > 0
        else 0.0
    )
    calmar = annualized / abs(max_drawdown) if max_drawdown < 0 else 0.0
    win_rate = float((returns > 0).mean()) if len(returns) else 0.0

    turnover = 0.0
    if trades:
        buy_amount = sum(
            float(t.quantity) * float(t.fill_price) for t in trades if t.side == "buy"
        )
        avg_equity = float(series.mean())
        turnover = buy_amount / avg_equity / years if avg_equity > 0 else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annualized,
        "max_drawdown": max_drawdown,
        "sharpe": sharpe,
        "calmar": calmar,
        "win_rate_daily": win_rate,
        "annual_turnover": turnover,
        "final_equity": float(series.iloc[-1]),
        "trading_days": len(series),
        "years": years,
    }


def load_benchmark(
    con: duckdb.DuckDBPyConnection, start: date, end: date, initial: float
) -> pd.Series | None:
    rows = con.execute(
        """
        SELECT trade_date, close
        FROM market.prices_qfq
        WHERE symbol = '000300.SH'
          AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        [start, end],
    ).fetchall()
    if len(rows) < 2:
        return None
    series = pd.Series(
        [float(r[1]) for r in rows], index=pd.to_datetime([r[0] for r in rows])
    )
    return series / series.iloc[0] * initial


def run_significance_test(
    equity_df: pd.DataFrame,
    compare_path: str,
    *,
    block_days: int,
    n_bootstrap: int,
    seed: int,
    self_label: str,
    other_label: str,
) -> dict[str, object]:
    """配对 block bootstrap：本 run 净值 vs 对照净值（如 inverse vs intent）。

    与 significance_test.py 同一口径：对齐交易日、日对数收益差分、按块长有放回
    重采样求总收益差的经验分布。仅在样本不足时返回带 error 的 dict，不抛异常。
    """
    import numpy as np

    from significance_test import block_bootstrap, load_equity

    s = equity_df.set_index(pd.to_datetime(equity_df["trade_date"]))[
        "total_equity"
    ].astype(float)
    b = load_equity(compare_path)
    idx = s.index.intersection(b.index)
    if len(idx) < 30:
        return {"error": f"共同交易日太少: {len(idx)}", "compare_equity": compare_path}

    diff = np.diff(np.log(s.loc[idx].values)) - np.diff(np.log(b.loc[idx].values))
    observed = float(diff.sum())
    boot = block_bootstrap(diff, block_days, n_bootstrap, seed)
    return {
        "compare_equity": compare_path,
        "labels": {"a": self_label, "b": other_label},
        "window_days": int(len(diff)),
        "block_days": block_days,
        "n_bootstrap": n_bootstrap,
        "observed_log_return_diff": round(observed, 6),
        "observed_total_return_diff": round(float(np.expm1(observed)), 6),
        "p_value_two_sided": round(float(2 * min((boot <= 0).mean(), (boot >= 0).mean())), 4),
        "p_value_one_sided_a_gt_b": round(float((boot <= 0).mean()), 4),
        "naive_daily_t_stat": round(
            float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff)))), 3
        ),
        "warning": "日度 t 统计量忽略自相关，仅供参考；以 block bootstrap 为准",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="低价超跌反弹策略历史回测")
    parser.add_argument("--start", default="2024-10-14")
    parser.add_argument("--end", default="2026-10-14")
    parser.add_argument("--rebalance-days", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--initial-cash", type=float, default=1_000_000)
    parser.add_argument("--max-price", type=float, default=15)
    parser.add_argument("--min-ret20", type=float, default=-0.35)
    parser.add_argument("--max-ret20", type=float, default=-0.15)
    parser.add_argument("--min-ret5", type=float, default=-0.05)
    parser.add_argument("--max-ret5", type=float, default=0.15)
    parser.add_argument("--min-avg-amount", type=float, default=10_000_000)
    parser.add_argument(
        "--rank-mode",
        choices=("inverse", "intent"),
        default="inverse",
        help="inverse=低价浅跌回踩（历史表现较好）；intent=低价深跌反弹",
    )
    parser.add_argument(
        "--include-financials",
        action="store_true",
        help="不排除银行/证券/保险/信托/期货/黄金等金融类",
    )
    parser.add_argument(
        "--compare-equity",
        default=None,
        help="对照净值 CSV（trade_date,total_equity 列），提供时做配对 block bootstrap 检验",
    )
    parser.add_argument(
        "--block-days",
        type=int,
        default=None,
        help="bootstrap 块长（交易日），默认取调仓周期",
    )
    parser.add_argument("--bootstrap", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out-dir", default=str(BACKEND_DIR.parent / "outputs" / "backtest")
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    with WARM_STORE.connect(control_db_path=DB_PATH, read_only=True) as con:
        trading_days = load_trading_days(con, start, end)
        signal_dates = build_rebalance_dates(trading_days, args.rebalance_days)
        print(f"交易日 {len(trading_days)} 个，信号日 {len(signal_dates)} 个")
        print(f"信号日样本：{signal_dates[:3]} ... {signal_dates[-2:]}")

        picks, selection = compute_signals(
            con,
            signal_dates,
            trading_days[max(0, trading_days.index(signal_dates[0]) - 30)],
            max_price=Decimal(str(args.max_price)),
            min_ret20=Decimal(str(args.min_ret20)),
            max_ret20=Decimal(str(args.max_ret20)),
            min_ret5=Decimal(str(args.min_ret5)),
            max_ret5=Decimal(str(args.max_ret5)),
            min_avg_amount=Decimal(str(args.min_avg_amount)),
            top_n=args.top_n,
            rank_mode=args.rank_mode,
            include_financials=args.include_financials,
        )
        if not selection:
            raise SystemExit("没有产生任何选股信号，请检查参数或数据范围")

        symbols = sorted({s for picks_list in selection.values() for s in picks_list})
        print(f"信号覆盖 {len(selection)} 期，候选股票 {len(symbols)} 只")

        bars = load_bars(con, symbols, signal_dates[0], end)
        calendar = build_execution_calendar(con, symbols, signal_dates[0], end)
        print(f"载入 bar {len(bars):,} 条")
        data_snapshot = fetch_data_fingerprint(con)

        weight = Decimal(1) / Decimal(args.top_n)
        signals = build_signals(selection, args.top_n, weight)

        engine = DailyEventBacktestEngine(
            initial_cash=Decimal(str(args.initial_cash)),
            fee_model=build_fee_model(
                fee_model_version=A_SHARE_COST_V1,
                commission_rate=Decimal("0.0003"),
                min_commission=Decimal("5"),
                sell_stamp_duty_rate=Decimal("0.0005"),
                slippage_bps=Decimal("2"),
            ),
        )
        result = engine.run(
            bars=bars, signals=signals, execution_calendar=calendar
        )

    metrics = compute_metrics(result.equity_curve, result.trades, result.initial_cash)
    print("\n=== 回测结果 ===")
    for key, value in metrics.items():
        if isinstance(value, float):
            print(f"{key:>20}: {value:,.4f}")
        else:
            print(f"{key:>20}: {value}")

    # 基准对比
    with WARM_STORE.connect(control_db_path=DB_PATH, read_only=True) as con:
        benchmark = load_benchmark(con, signal_dates[0], end, args.initial_cash)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (
        f"{args.start}_{args.end}_{args.rebalance_days}d_top{args.top_n}"
        f"_{args.rank_mode}"
        + ("_fin" if args.include_financials else "")
    )

    equity_df = pd.DataFrame(
        [
            {
                "trade_date": p.trade_date,
                "cash": float(p.cash),
                "market_value": float(p.market_value),
                "total_equity": float(p.total_equity),
            }
            for p in result.equity_curve
        ]
    )
    if benchmark is not None:
        bench_df = benchmark.rename("benchmark_equity").reset_index()
        bench_df.columns = ["trade_date", "benchmark_equity"]
        bench_df["trade_date"] = pd.to_datetime(bench_df["trade_date"]).dt.date
        equity_df = equity_df.merge(bench_df, on="trade_date", how="left")
        bench_total = (
            float(benchmark.iloc[-1]) / args.initial_cash - 1 if len(benchmark) else None
        )
        if bench_total is not None:
            metrics["benchmark_total_return"] = bench_total
            metrics["excess_return"] = metrics["total_return"] - bench_total

    significance: dict[str, object] | None = None
    if args.compare_equity:
        significance = run_significance_test(
            equity_df,
            args.compare_equity,
            block_days=args.block_days or args.rebalance_days,
            n_bootstrap=args.bootstrap,
            seed=args.seed,
            self_label=args.rank_mode,
            other_label=Path(args.compare_equity).stem,
        )
        print("\n=== 显著性检验（配对 block bootstrap）===")
        print(json.dumps(significance, indent=2, ensure_ascii=False))

    equity_df.to_csv(out_dir / f"equity_{stamp}.csv", index=False, encoding="utf-8-sig")
    picks.to_csv(out_dir / f"picks_{stamp}.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                "signal_date": t.signal_date,
                "trade_date": t.trade_date,
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "reference_price": float(t.reference_price),
                "fill_price": float(t.fill_price),
                "commission": float(t.commission),
                "cash_after": float(t.cash_after),
            }
            for t in result.trades
        ]
    ).to_csv(out_dir / f"trades_{stamp}.csv", index=False, encoding="utf-8-sig")

    summary = {
        "params": vars(args),
        "metrics": metrics,
        "signal_periods": len(selection),
        "candidate_symbols": len(symbols),
        "trade_count": len(result.trades),
        "data_snapshot": data_snapshot,
        **({"significance_test": significance} if significance else {}),
    }
    (out_dir / f"summary_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n结果已保存到 {out_dir}")


if __name__ == "__main__":
    main()
