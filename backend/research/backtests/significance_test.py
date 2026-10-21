"""paired block-bootstrap 显著性检验：inverse vs intent 的净值差异是否显著。

用法:
    python backend/research/backtests/significance_test.py \
        --equity-a outputs/backtest/equity_<...>_inverse.csv \
        --equity-b outputs/backtest/equity_<...>_intent.csv \
        --block-days 20 --bootstrap 20000

原理:
- 对齐两条日净值曲线，取每日对数收益的差值序列 d_t（配对样本，剔除共同的市场因素）。
- 按调仓周期长度（默认 20 个交易日）分块 bootstrap：有放回地抽取整块、拼接成与样本等长的序列，
  求和得到「总对数收益差」的一个 bootstrap 实现，重复 N 次得到经验分布。
- 报告: 观测到的总收益差、双侧/单侧经验 p 值、日度差值的 t 统计量。
- 注意: 块长 = 调仓周期，因为持仓在周期内高度自相关；若策略持仓重叠周期更长，块长应相应加大。
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def load_equity(path: str) -> pd.Series:
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    s = df.set_index("trade_date")["total_equity"].astype(float)
    return s.sort_index()


def block_bootstrap(diff: np.ndarray, block: int, n_boot: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = len(diff)
    n_blocks = int(np.ceil(n / block))
    sums = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n - block + 1, size=n_blocks)
        sample = np.concatenate([diff[s : s + block] for s in starts])[:n]
        sums[i] = sample.sum()
    return sums


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--equity-a", required=True, help="净值曲线 A（如 inverse）")
    parser.add_argument("--equity-b", required=True, help="净值曲线 B（如 intent）")
    parser.add_argument("--block-days", type=int, default=20)
    parser.add_argument("--bootstrap", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = parser.parse_args()

    a = load_equity(args.equity_a)
    b = load_equity(args.equity_b)
    idx = a.index.intersection(b.index)
    if len(idx) < 30:
        raise SystemExit(f"共同交易日太少: {len(idx)}")

    la = np.log(a.loc[idx].values)
    lb = np.log(b.loc[idx].values)
    # 日度对数收益（首日为建仓日无收益，丢弃）
    ra = np.diff(la)
    rb = np.diff(lb)
    diff = ra - rb
    observed = float(diff.sum())

    boot = block_bootstrap(diff, args.block_days, args.bootstrap, args.seed)
    p_two = float(2 * min((boot <= 0).mean(), (boot >= 0).mean()))
    p_one = float((boot <= 0).mean())  # H1: A > B

    t_stat = float(diff.mean() / (diff.std(ddof=1) / np.sqrt(len(diff))))

    result = {
        "window_days": int(len(diff)),
        "block_days": args.block_days,
        "n_bootstrap": args.bootstrap,
        "observed_log_return_diff": round(observed, 6),
        "observed_total_return_diff": round(np.expm1(observed), 6),
        "p_value_two_sided": round(p_two, 4),
        "p_value_one_sided_a_gt_b": round(p_one, 4),
        "naive_daily_t_stat": round(t_stat, 3),
        "warning": "日度 t 统计量忽略自相关，仅供参考；以 block bootstrap 为准",
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
