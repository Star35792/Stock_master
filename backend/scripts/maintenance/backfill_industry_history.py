"""行业时点标签首切片回填（P2-3）。

为 ``market.security`` 中已有当前行业标签的股票写入
``market.security_industry_history`` 单区间（``effective_from`` → NULL），
让 ``industries_as_of`` 的 PIT 读路径在全历史命中。

诚实披露（这是**当前标签回填**，不是真实历史）：
- 行业标签来自 2026-10-16 腾讯自选股手工补数（5,582/5,584 active 股票），
  没有行业变更历史数据源；本脚本把当前标签从 ``list_date``（缺失时从温层
  首条 bar 日期）起生效，等价假设「行业从未变更」。
- 行业迁移（如通信设备 → 消费电子）在历史上存在，本近似对行业中性化 /
  行业预算的影响无法量化；``source_name`` 固定为
  ``current_label_backfill_20261024`` 供后续区分与重建。
- 未来接入真实行业历史数据源后，用 ``record_interval`` 追加区间即可，
  本脚本写入的区间会被正确关闭。

幂等：已存在该 symbol 区间的行跳过，重复运行安全。
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.database.session import SessionLocal  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

SOURCE_NAME = "current_label_backfill_20261024"

DEFAULT_WARM_MARKET_GLOB = "data/warm/exported/20261021-1800/market/daily_bar/**/*.parquet"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="行业时点标签首切片回填")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--warm-market-glob",
        default=DEFAULT_WARM_MARKET_GLOB,
        help="温层日线 parquet glob，用于 list_date 缺失时的首条 bar 日期",
    )
    args = parser.parse_args(argv)

    with duckdb_write_lock(timeout_seconds=120):
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        securities = db.execute(
            text("""
            SELECT symbol, industry, list_date FROM market.security
            WHERE industry IS NOT NULL AND industry <> ''
        """)
        ).all()
        existing = {
            row[0]
            for row in db.execute(
                text("SELECT DISTINCT symbol FROM market.security_industry_history")
            ).all()
        }
        pending = [
            (symbol, industry, list_date)
            for symbol, industry, list_date in securities
            if symbol not in existing
        ]
        from_list_date = [row for row in pending if row[2] is not None]
        need_first_bar = [row for row in pending if row[2] is None]
        print(
            f"有当前行业标签: {len(securities)}；已有时点区间跳过: "
            f"{len(securities) - len(pending)}；待写入: {len(pending)}"
            f"（list_date 直接可用 {len(from_list_date)}，需查温层首条 bar "
            f"{len(need_first_bar)}）"
        )

        first_bars: dict[str, date] = {}
        if need_first_bar:
            glob_arg = args.warm_market_glob.replace("'", "''")
            warm_rows = db.execute(
                text(f"""
                SELECT symbol, min(trade_date) FROM read_parquet('{glob_arg}')
                GROUP BY symbol
            """)
            ).all()
            first_bars = dict(warm_rows)

        inserts: list[tuple[str, str, date]] = []
        unresolved = 0
        for symbol, industry, list_date in pending:
            effective_from = list_date or first_bars.get(symbol)
            if effective_from is None:
                unresolved += 1
                continue
            inserts.append((symbol, industry, effective_from))
        print(f"可写入区间: {len(inserts)}；无法定位生效日: {unresolved}")
        if args.dry_run or not inserts:
            return 0

        from app.models.market import SecurityIndustryHistory

        db.add_all(
            SecurityIndustryHistory(
                symbol=symbol,
                industry=industry,
                effective_from=effective_from,
                effective_to=None,
                source_name=SOURCE_NAME,
            )
            for symbol, industry, effective_from in inserts
        )
        db.commit()
        inserted = db.execute(
            text(
                "SELECT count(*) FROM market.security_industry_history WHERE source_name = :source"
            ),
            {"source": SOURCE_NAME},
        ).scalar()
        print(f"完成：source={SOURCE_NAME} 共 {inserted} 行")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
