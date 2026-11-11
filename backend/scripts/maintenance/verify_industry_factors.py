"""独立重算校验：行业因子值 vs 从原始 bar + 时点行业标签重算的结果。

为什么要有这个脚本
------------------
因子服务把「标的 → 行业 → 行业聚合」三步串在一起，中间任何一步的口径错了
都不会报错，只会安静地产出错误的数字。这里绕开服务，**只用原始数据**重算一遍：

* 动量/宽度：从 ``market.daily_bar`` 取每只标的窗口内第 N+1 根与第 1 根的
  收盘价比，按**时点行业**聚合，再与落库值逐行比对；
* 行业成交额占比变化：先算行业成交额占比，再与 20 个观测前的占比相减。

输入：``--factor`` 指定要校验的因子名。
输出：比对行数、完全一致行数、最大绝对偏差、最大相对偏差。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

DEFAULT_DB = "data/stock_master.duckdb"

#: 各因子的回看窗口（交易日）。
HORIZONS = {
    "industry_momentum_60": 60,
    "industry_breadth_20": 20,
}


def _pit_label_cte() -> str:
    """时点行业标签：命中历史区间用区间标签，否则回退当前标签。"""

    return """
    pit AS (
        SELECT symbol, industry, effective_from, effective_to
        FROM market.security_industry_history
    ),
    labelled AS (
        SELECT b.symbol, b.trade_date, b.close, b.rn,
               COALESCE(p.industry, s.industry) AS industry
        FROM bars b
        JOIN market.security s ON s.symbol = b.symbol
        LEFT JOIN pit p
          ON p.symbol = b.symbol
         AND p.effective_from <= b.trade_date
         AND (p.effective_to IS NULL OR p.effective_to > b.trade_date)
    )
    """


def check_momentum(con: duckdb.DuckDBPyConnection, factor_name: str, horizon: int) -> None:
    query = f"""
    WITH bars AS (
        SELECT symbol, trade_date, close,
               row_number() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn
        FROM market.daily_bar
        WHERE adjust_type = 'none'
          AND quality_status = 'trusted'
          AND is_suspect IS FALSE
    ),
    {_pit_label_cte()},
    ret AS (
        SELECT l.symbol, l.trade_date, l.industry,
               l.close / p.close - 1 AS r
        FROM labelled l
        JOIN bars p ON p.symbol = l.symbol AND p.rn = l.rn - {horizon}
        WHERE l.close IS NOT NULL AND p.close IS NOT NULL AND p.close <> 0
    ),
    agg AS (
        SELECT trade_date, industry, AVG(r) AS mean_r FROM ret GROUP BY 1, 2
    )
    SELECT
        (SELECT count(*) FROM factor.factor_value
          WHERE factor_name = '{factor_name}') AS stored_rows,
        count(*) AS compared,
        sum(CASE WHEN ABS(a.mean_r - f.value) <= 1e-9 THEN 1 ELSE 0 END) AS matched,
        max(ABS(a.mean_r - f.value)) AS max_abs_diff,
        max(CASE WHEN ABS(a.mean_r) > 1e-12
                 THEN ABS(a.mean_r - f.value) / ABS(a.mean_r) ELSE 0 END) AS max_rel_diff
    FROM factor.factor_value f
    JOIN agg a
      ON a.trade_date = f.trade_date
     AND a.industry = (
         SELECT industry FROM labelled l2
          WHERE l2.symbol = f.symbol AND l2.trade_date = f.trade_date
          LIMIT 1)
    WHERE f.factor_name = '{factor_name}'
    """
    stored, compared, matched, abs_diff, rel_diff = con.execute(query).fetchone()
    print(f"--- {factor_name} ---")
    print(f"  落库行数     : {stored}")
    print(f"  可比对行数   : {compared}")
    print(f"  完全一致行数 : {matched}")
    print(f"  最大绝对偏差 : {abs_diff}")
    print(f"  最大相对偏差 : {rel_diff}")


def check_breadth(con: duckdb.DuckDBPyConnection, factor_name: str, horizon: int) -> None:
    query = f"""
    WITH bars AS (
        SELECT symbol, trade_date, close,
               row_number() OVER (PARTITION BY symbol ORDER BY trade_date) AS rn
        FROM market.daily_bar
        WHERE adjust_type = 'none'
          AND quality_status = 'trusted'
          AND is_suspect IS FALSE
    ),
    {_pit_label_cte()},
    ret AS (
        SELECT l.symbol, l.trade_date, l.industry,
               l.close / p.close - 1 AS r
        FROM labelled l
        JOIN bars p ON p.symbol = l.symbol AND p.rn = l.rn - {horizon}
        WHERE l.close IS NOT NULL AND p.close IS NOT NULL AND p.close <> 0
    ),
    agg AS (
        SELECT trade_date, industry,
               CAST(SUM(CASE WHEN r > 0 THEN 1 ELSE 0 END) AS DOUBLE) / COUNT(*) AS breadth
        FROM ret GROUP BY 1, 2
    )
    SELECT
        (SELECT count(*) FROM factor.factor_value
          WHERE factor_name = '{factor_name}') AS stored_rows,
        count(*) AS compared,
        sum(CASE WHEN ABS(a.breadth - f.value) <= 1e-9 THEN 1 ELSE 0 END) AS matched,
        max(ABS(a.breadth - f.value)) AS max_abs_diff
    FROM factor.factor_value f
    JOIN agg a
      ON a.trade_date = f.trade_date
     AND a.industry = (
         SELECT industry FROM labelled l2
          WHERE l2.symbol = f.symbol AND l2.trade_date = f.trade_date
          LIMIT 1)
    WHERE f.factor_name = '{factor_name}'
    """
    stored, compared, matched, abs_diff = con.execute(query).fetchone()
    print(f"--- {factor_name} ---")
    print(f"  落库行数     : {stored}")
    print(f"  可比对行数   : {compared}")
    print(f"  完全一致行数 : {matched}")
    print(f"  最大绝对偏差 : {abs_diff}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Independently recompute industry factors")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument(
        "--factor",
        default="industry_momentum_60",
        choices=sorted(HORIZONS),
    )
    args = parser.parse_args()

    con = duckdb.connect(args.db, read_only=True)
    try:
        if args.factor == "industry_breadth_20":
            check_breadth(con, args.factor, HORIZONS[args.factor])
        else:
            check_momentum(con, args.factor, HORIZONS[args.factor])
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
