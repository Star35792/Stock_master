"""Run the low-price oversold-rebound screen for the latest available session.

Reads ``low_price_oversold_rebound.sql`` (single source of truth for the rule),
substitutes parameters, enriches with industry/ST/price-change context, and
writes a CSV snapshot plus a printed digest.

Usage::

    python backend/research/screens/run_screen.py --top-n 20
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DB_PATH = REPO / "backend" / "data" / "stock_master.duckdb"
SQL_PATH = HERE / "low_price_oversold_rebound.sql"


def render_sql(**params: object) -> str:
    sql = SQL_PATH.read_text(encoding="utf-8")
    for key, value in params.items():
        sql = sql.replace(f":{key}", str(value))
    return sql


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-price", default="15")
    parser.add_argument("--min-ret20", default="-0.35")
    parser.add_argument("--max-ret20", default="-0.15")
    parser.add_argument("--min-ret5", default="-0.05")
    parser.add_argument("--max-ret5", default="0.15")
    parser.add_argument("--min-avg-amount", default="10000000")
    parser.add_argument("--top-n", default="20")
    parser.add_argument("--out-dir", default=str(REPO / "outputs" / "screens"))
    args = parser.parse_args()

    sql = render_sql(
        max_price=Decimal(args.max_price),
        min_ret20=Decimal(args.min_ret20),
        max_ret20=Decimal(args.max_ret20),
        min_ret5=Decimal(args.min_ret5),
        max_ret5=Decimal(args.max_ret5),
        min_avg_amount=int(args.min_avg_amount),
        top_n=int(args.top_n),
    ).rstrip().rstrip(";")

    with duckdb.connect(str(DB_PATH), read_only=True) as con:
        latest = con.execute(
            "SELECT max(trade_date) FROM market.daily_bar WHERE adjust_type='qfq'"
        ).fetchone()[0]
        df = con.execute(
            f"""
            SELECT b.*, d.pct_change, d.pre_close, d.turnover_rate,
                   d.amount AS amount_today, d.volume AS volume_today,
                   d.special_treatment_status
            FROM ({sql}) b
            LEFT JOIN market.daily_bar d
              ON d.symbol = b.symbol AND d.trade_date = '{latest}' AND d.adjust_type = 'qfq'
            ORDER BY b.score DESC
            """
        ).fetchdf()
        # qfq close differs from raw close once a dividend/split happens; keep raw for display
        raw = con.execute(
            f"""
            SELECT symbol, close AS close_none FROM market.daily_bar
            WHERE trade_date = '{latest}' AND adjust_type = 'none'
            """
        ).fetchdf()
        breadth = con.execute(
            f"""
            SELECT count(*) n,
                   sum(CASE WHEN pct_change > 0 THEN 1 ELSE 0 END) up,
                   sum(CASE WHEN pct_change < 0 THEN 1 ELSE 0 END) down,
                   sum(CASE WHEN pct_change = 0 THEN 1 ELSE 0 END) flat,
                   median(pct_change) med
            FROM market.daily_bar
            WHERE trade_date = '{latest}' AND adjust_type = 'qfq' AND security_type = 'stock'
            """
        ).fetchdf().to_dict(orient="records")[0]

    df = df.drop(columns=["close_raw"], errors="ignore").merge(raw, on="symbol", how="left")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().strftime("%Y%m%d")
    csv_path = out_dir / f"low_price_oversold_{stamp}_{latest}.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")

    payload = {
        "trade_date": str(latest),
        "matches": int(len(df)),
        "breadth": {k: (float(v) if isinstance(v, (int, float)) and v is not None else v) for k, v in breadth.items()},
        "csv": str(csv_path),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if df.empty:
        print("no matches for the current rule")
        return 0

    show = ["symbol", "name", "industry", "p_latest", "close_none", "ret5", "ret20",
            "avg_amount_20", "pct_change", "score"]
    with pd.option_context("display.width", 240, "display.max_columns", 50):
        print(df[[c for c in show if c in df.columns]].to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
