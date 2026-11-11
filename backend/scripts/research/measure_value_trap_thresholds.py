"""Measure the market-wide distribution behind the value-trap thresholds.

This script is the reproducible source for the "threshold basis" table of
the value-investing strategy contract
(``aiDoc/strategy/20260922-1314-price-value-margin-strategy.md``).  Every
count quoted in that document can be regenerated here:

    python -m scripts.research.measure_value_trap_thresholds
    python -m scripts.research.measure_value_trap_thresholds --target 2026-11-09

Population: every security in the control plane that has annual-report
evidence for the four-year trend window (FY of target.year-4 .. target.year-1)
and a disclosure date on or before the target day.  This is deliberately
*wider* than the screen universe (which starts from securities with a
trusted price on the target day) so that the threshold choice is calibrated
on the market, not on the day's price survivors.

Trend semantics are the known-minimum decline streaks shared with
``app/services/value_investing_screen.py`` through
``app.services.value_trap_trend``: a decline streak counts *observed*
consecutive year-on-year declines backwards from the latest period, and a
missing period stops the count instead of fabricating a longer streak.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

from app.services.value_trap_trend import (
    DEBT_RATIO_CHANGE_SQL,
    NET_PROFIT_DECLINE_STREAK_SQL,
    REVENUE_DECLINE_STREAK_SQL,
)

DEFAULT_TARGET = date(2026, 11, 9)


def _trend_sql(target: date) -> str:
    year = target.year
    base, p1, p2, last = (
        date(year - 4, 12, 31),
        date(year - 3, 12, 31),
        date(year - 2, 12, 31),
        date(year - 1, 12, 31),
    )
    periods = f"DATE '{base}', DATE '{p1}', DATE '{p2}', DATE '{last}'"
    return f"""
        WITH statements AS (
            SELECT symbol, period_end, statement_type,
                   try_cast(json_extract_string(metrics,
                       '$.parent_holder_net_profit') AS DOUBLE) AS net_profit,
                   try_cast(json_extract_string(metrics,
                       '$.operating_income') AS DOUBLE) AS revenue,
                   try_cast(json_extract_string(metrics,
                       '$.holder_equity_total') AS DOUBLE) AS equity,
                   try_cast(json_extract_string(metrics,
                       '$.assets_total') AS DOUBLE) AS assets_total
            FROM fundamental.financial_statement
            WHERE report_date <= DATE '{target}'
            QUALIFY row_number() OVER (
                PARTITION BY symbol, statement_type, period_end
                ORDER BY report_date DESC NULLS LAST
            ) = 1
        ),
        annual_revenue AS (
            SELECT symbol,
                   count(*) FILTER (WHERE revenue IS NOT NULL)
                       AS revenue_periods_present,
                   max(revenue) FILTER (WHERE period_end = DATE '{base}')
                       AS rev_base,
                   max(revenue) FILTER (WHERE period_end = DATE '{p1}')
                       AS rev_p1,
                   max(revenue) FILTER (WHERE period_end = DATE '{p2}')
                       AS rev_p2,
                   max(revenue) FILTER (WHERE period_end = DATE '{last}')
                       AS rev_last
            FROM statements
            WHERE statement_type = 'income'
              AND period_end IN ({periods})
            GROUP BY symbol
        ),
        annual_profit AS (
            SELECT symbol,
                   count(*) FILTER (WHERE net_profit IS NOT NULL)
                       AS profit_periods_present,
                   max(net_profit) FILTER (WHERE period_end = DATE '{base}')
                       AS np_base,
                   max(net_profit) FILTER (WHERE period_end = DATE '{p1}')
                       AS np_p1,
                   max(net_profit) FILTER (WHERE period_end = DATE '{p2}')
                       AS np_p2,
                   max(net_profit) FILTER (WHERE period_end = DATE '{last}')
                       AS np_last
            FROM statements
            WHERE statement_type = 'income'
              AND period_end IN ({periods})
            GROUP BY symbol
        ),
        annual_balance AS (
            SELECT symbol,
                   max(assets_total) FILTER (WHERE period_end = DATE '{base}')
                       AS assets_base,
                   max(equity) FILTER (WHERE period_end = DATE '{base}')
                       AS equity_base,
                   max(assets_total) FILTER (WHERE period_end = DATE '{last}')
                       AS assets_last,
                   max(equity) FILTER (WHERE period_end = DATE '{last}')
                       AS equity_last
            FROM statements
            WHERE statement_type = 'balance'
              AND period_end IN (DATE '{base}', DATE '{last}')
            GROUP BY symbol
        ),
        trend AS (
            SELECT ar.symbol,
                   least(ar.revenue_periods_present, ap.profit_periods_present)
                       AS trend_periods_present,
                   {REVENUE_DECLINE_STREAK_SQL} AS revenue_decline_streak,
                   {NET_PROFIT_DECLINE_STREAK_SQL} AS net_profit_decline_streak,
                   {DEBT_RATIO_CHANGE_SQL} AS debt_ratio_change_3y
            FROM annual_revenue ar
            JOIN annual_profit ap ON ap.symbol = ar.symbol
            LEFT JOIN annual_balance ab ON ab.symbol = ar.symbol
        )
        SELECT
            count(*) AS securities_with_trend_evidence,
            count(*) FILTER (WHERE trend_periods_present = 4)
                AS complete_window,
            count(*) FILTER (WHERE net_profit_decline_streak >= 1)
                AS net_profit_decline_ge1,
            count(*) FILTER (WHERE net_profit_decline_streak >= 2)
                AS net_profit_decline_ge2,
            count(*) FILTER (WHERE net_profit_decline_streak >= 3)
                AS net_profit_decline_ge3,
            count(*) FILTER (WHERE revenue_decline_streak >= 1)
                AS revenue_decline_ge1,
            count(*) FILTER (WHERE revenue_decline_streak >= 2)
                AS revenue_decline_ge2,
            count(*) FILTER (WHERE revenue_decline_streak >= 3)
                AS revenue_decline_ge3,
            count(*) FILTER (
                WHERE net_profit_decline_streak >= 2
                  AND revenue_decline_streak >= 2) AS both_decline_ge2,
            count(*) FILTER (
                WHERE net_profit_decline_streak >= 2
                  AND revenue_decline_streak >= 1) AS profit_ge2_revenue_ge1,
            count(*) FILTER (WHERE debt_ratio_change_3y IS NOT NULL)
                AS debt_change_known,
            count(*) FILTER (WHERE debt_ratio_change_3y > 0.05)
                AS debt_change_gt_5pp,
            count(*) FILTER (WHERE debt_ratio_change_3y > 0.10)
                AS debt_change_gt_10pp,
            quantile_cont(debt_ratio_change_3y, 0.5) AS debt_change_median,
            quantile_cont(debt_ratio_change_3y, 0.75) AS debt_change_p75,
            quantile_cont(debt_ratio_change_3y, 0.90) AS debt_change_p90
        FROM trend
    """


COLUMNS = (
    ("securities_with_trend_evidence", "有趋势证据的证券数"),
    ("complete_window", "其中 4 期年报齐备"),
    ("net_profit_decline_ge1", "净利连跌 >= 1 年"),
    ("net_profit_decline_ge2", "净利连跌 >= 2 年"),
    ("net_profit_decline_ge3", "净利连跌 >= 3 年"),
    ("revenue_decline_ge1", "营收连跌 >= 1 年"),
    ("revenue_decline_ge2", "营收连跌 >= 2 年"),
    ("revenue_decline_ge3", "营收连跌 >= 3 年"),
    ("both_decline_ge2", "营收与净利同时连跌 >= 2 年"),
    ("profit_ge2_revenue_ge1", "[审计] 净利连跌>=2 且 营收连跌>=1"),
    ("debt_change_known", "负债率 3 年变化已知"),
    ("debt_change_gt_5pp", "负债率 3 年上升 > 5pp"),
    ("debt_change_gt_10pp", "负债率 3 年上升 > 10pp"),
    ("debt_change_median", "负债率 3 年变化中位数"),
    ("debt_change_p75", "负债率 3 年变化 p75"),
    ("debt_change_p90", "负债率 3 年变化 p90"),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=date.fromisoformat,
        default=DEFAULT_TARGET,
        help="evidence cut-off day (default: 2026-11-09)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/stock_master.duckdb"),
        help="control-plane DuckDB path (default: data/stock_master.duckdb)",
    )
    args = parser.parse_args()

    connection = duckdb.connect(str(args.db), read_only=True)
    try:
        row = connection.execute(_trend_sql(args.target)).fetchone()
    finally:
        connection.close()

    values = dict(zip((key for key, _ in COLUMNS), row, strict=True))
    print(f"target={args.target}  db={args.db}")
    print(f"{'指标':<38}{'数值':>16}")
    for key, label in COLUMNS:
        value = values[key]
        if isinstance(value, float):
            rendered = f"{value * 100:+.2f}pp"
        else:
            rendered = str(value)
        print(f"{label:<38}{rendered:>16}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
