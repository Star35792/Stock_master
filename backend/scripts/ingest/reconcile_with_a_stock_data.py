"""跨源对账：用 a-stock-data 独立源核验 market 层的可信度。

对账对象
--------
1. **收盘价**：通达信官网盘后包（独立厂商）vs ``market.daily_bar``（同花顺源）
2. **成交量 / 成交额**：同上——这是最容易暴露单位口径错误的字段
3. **复权因子**：新浪复权因子 vs ``market.adjustment_factor``（生产版本 v1）

单位口径注意
------------
通达信盘后包的 ``volume`` 是**股**；项目 ``market.daily_bar.volume`` 对个股也是股，
但对**指数类**标的存的是「手」。因此指数会呈现精确的 100 倍（或 10000 倍）差异——
这是口径差，不是数据错。脚本按 ``security_type`` 分组统计，避免把口径差误判为冲突。

复权因子注意
------------
新浪 qfq 因子是**除数**（前复权价 = 不复权价 ÷ factor），项目 v1 因子是**乘数**
（前复权价 = 不复权价 × factor）。两者互为倒数，对账必须先统一方向再比数值。

用法::

    python scripts/ingest/reconcile_with_a_stock_data.py --trade-date 2026-11-10
    python scripts/ingest/reconcile_with_a_stock_data.py --trade-date 2026-11-10 --write
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "libs"))

from a_stock_data.loader import load  # noqa: E402

DEFAULT_DB = REPO_ROOT / "backend" / "data" / "stock_master.duckdb"

#: 生产路径使用的复权因子版本（curated 层按此过滤）
FACTOR_SOURCE = "hithink_finance_dump"
FACTOR_VERSION = "v1"


def _market_prefix(market: str) -> str:
    return {"sh": "SH", "sz": "SZ", "bj": "BJ"}.get(str(market).lower(), "SZ")


def fetch_tdx_package(mod, ymd: str) -> pd.DataFrame:
    """取通达信官网盘后包并归一化列名与 symbol 格式。"""
    frame = mod.tdx_daily_package(ymd)
    frame = frame.rename(columns={c: c.lower() for c in frame.columns})
    frame["code6"] = frame["code"].astype(str).str.zfill(6)
    frame["symbol"] = frame.apply(
        lambda r: f"{r['code6']}.{_market_prefix(r['market'])}", axis=1
    )
    for col in ("close", "volume", "amount"):
        frame[f"tdx_{col}"] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def reconcile_daily_bar(con, pkg: pd.DataFrame, trade_date: str) -> dict:
    """日线三字段对拍：收盘价 / 成交量 / 成交额。"""
    market = con.execute(
        """
        SELECT symbol, close, volume, amount, source_name, quality_status
        FROM market.daily_bar
        WHERE trade_date = ? AND adjust_type = 'none'
        """,
        [trade_date],
    ).fetchdf()
    joined = market.merge(
        pkg[["symbol", "tdx_close", "tdx_volume", "tdx_amount", "name"]],
        on="symbol",
        how="inner",
    )
    if joined.empty:
        return {"matched": 0}

    security = con.execute(
        "SELECT symbol, name, security_type FROM market.security"
    ).fetchdf()
    joined = joined.merge(
        security.rename(columns={"name": "sec_name"}), on="symbol", how="left"
    )

    joined["close_diff"] = (joined["close"].astype(float) - joined["tdx_close"]).abs()
    joined["vol_ratio"] = joined["volume"].astype(float) / joined["tdx_volume"].astype(float)
    joined["amt_ratio"] = joined["amount"].astype(float) / joined["tdx_amount"].astype(float)

    def _rate(series: pd.Series, threshold: float) -> tuple[int, float]:
        valid = series.notna() & (series > 0)
        if not valid.any():
            return 0, 0.0
        hit = (series[valid].sub(1).abs() < threshold).sum()
        return int(hit), float(hit / valid.sum())

    vol_exact, vol_exact_rate = _rate(joined["vol_ratio"], 1e-9)
    vol_close, vol_close_rate = _rate(joined["vol_ratio"], 0.01)
    amt_exact, amt_exact_rate = _rate(joined["amt_ratio"], 1e-9)
    amt_close, amt_close_rate = _rate(joined["amt_ratio"], 0.01)

    stocks = joined[joined["security_type"] == "stock"]
    stock_vol_exact, stock_vol_rate = _rate(stocks["vol_ratio"], 1e-9)

    close_over = joined[joined["close_diff"] > 0.011]
    return {
        "matched": len(joined),
        "close_exact": int((joined["close_diff"] < 1e-6).sum()),
        "close_within_cent": int((joined["close_diff"] <= 0.011).sum()),
        "close_conflicts": close_over,
        "vol_exact": vol_exact,
        "vol_exact_rate": vol_exact_rate,
        "vol_within_1pct": vol_close,
        "vol_within_1pct_rate": vol_close_rate,
        "amt_exact": amt_exact,
        "amt_exact_rate": amt_exact_rate,
        "amt_within_1pct": amt_close,
        "amt_within_1pct_rate": amt_close_rate,
        "stock_count": len(stocks),
        "stock_vol_exact": stock_vol_exact,
        "stock_vol_exact_rate": stock_vol_rate,
        "non_stock": joined[joined["security_type"] != "stock"],
    }


def reconcile_adjustment_factor(con, mod, symbols: list[str]) -> pd.DataFrame:
    """复权因子对拍（先统一方向：新浪是除数，项目 v1 是乘数）。"""
    rows = []
    for symbol in symbols:
        code = symbol.split(".")[0]
        try:
            sina_steps = mod.sina_adjust_factor(code, "qfq")
        except Exception as exc:
            rows.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if not sina_steps:
            rows.append({"symbol": symbol, "error": "新浪返回空"})
            continue

        steps = sorted((r["date"], r["factor"]) for r in sina_steps)
        project = con.execute(
            """
            SELECT trade_date, factor FROM market.adjustment_factor
            WHERE symbol = ? AND adjust_type = 'qfq'
              AND source_name = ? AND version = ?
            ORDER BY trade_date DESC LIMIT 400
            """,
            [symbol, FACTOR_SOURCE, FACTOR_VERSION],
        ).fetchdf()

        compared = []
        for _, row in project.iterrows():
            day = str(row["trade_date"])[:10]
            value = None
            for effective_day, factor in steps:
                if effective_day <= day:
                    value = factor
                else:
                    break
            if value is not None:
                compared.append((day, float(row["factor"]), float(value)))
        if not compared:
            rows.append({"symbol": symbol, "error": "无可比日"})
            continue

        frame = pd.DataFrame(compared, columns=["date", "project", "sina"])
        # 方向归一：新浪 qfq 因子是除数，项目 v1 因子是乘数 → 比较 1/project 与 sina
        frame["sina_aligned"] = frame["sina"]
        frame["project_aligned"] = 1.0 / frame["project"]
        frame["abs_diff"] = (frame["project_aligned"] - frame["sina_aligned"]).abs()
        frame["rel_diff"] = frame["abs_diff"] / frame["sina_aligned"].abs().clip(lower=1e-12)
        rows.append(
            {
                "symbol": symbol,
                "compared_days": len(frame),
                "exact": int((frame["abs_diff"] < 1e-9).sum()),
                "max_abs_diff": float(frame["abs_diff"].max()),
                "max_rel_diff": float(frame["rel_diff"].max()),
                "worst_date": str(frame.loc[frame["abs_diff"].idxmax(), "date"]),
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="a-stock-data 跨源对账")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--trade-date", required=True, help="交易日 YYYY-MM-DD")
    parser.add_argument(
        "--symbols",
        default="601799.SH,600519.SH,000001.SZ,000858.SZ,300750.SZ,601398.SH",
        help="复权因子对拍标的（逗号分隔）",
    )
    args = parser.parse_args()

    mod, skipped = load()
    if skipped:
        print(f"[loader] {len(skipped)} 个代码块被跳过（多为可选依赖缺失）")

    ymd = args.trade_date.replace("-", "")
    con = duckdb.connect(args.db, read_only=True)
    try:
        pkg = fetch_tdx_package(mod, ymd)
        result = reconcile_daily_bar(con, pkg, args.trade_date)

        print(f"\n=== 日线对拍 {args.trade_date}（通达信官网盘后包 vs market.daily_bar）===")
        print(f"  可对拍标的: {result['matched']}")
        print(
            f"  收盘价: 完全一致 {result['close_exact']}, "
            f"1分以内 {result['close_within_cent']}, 超阈 {len(result['close_conflicts'])}"
        )
        print(
            f"  成交量: 完全一致 {result['vol_exact']} ({result['vol_exact_rate']:.2%}), "
            f"<1% {result['vol_within_1pct']} ({result['vol_within_1pct_rate']:.2%})"
        )
        print(
            f"  成交额: 完全一致 {result['amt_exact']} ({result['amt_exact_rate']:.2%}), "
            f"<1% {result['amt_within_1pct']} ({result['amt_within_1pct_rate']:.2%})"
        )
        print(
            f"  个股子集: {result['stock_count']} 只, "
            f"成交量完全一致 {result['stock_vol_exact']} ({result['stock_vol_exact_rate']:.2%})"
        )
        if len(result["close_conflicts"]):
            print("\n  收盘价差异明细:")
            print(
                result["close_conflicts"][
                    ["symbol", "close", "tdx_close", "close_diff", "name"]
                ].to_string(index=False)
            )
        non_stock = result["non_stock"]
        if len(non_stock):
            print(
                f"\n  非个股标的 {len(non_stock)} 只 —— 成交量口径按手/股差异属正常，"
                f"勿判为冲突"
            )

        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
        factors = reconcile_adjustment_factor(con, mod, symbols)
        print("\n=== 复权因子对拍（新浪 vs market.adjustment_factor v1，已归一方向）===")
        print(factors.to_string(index=False))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
