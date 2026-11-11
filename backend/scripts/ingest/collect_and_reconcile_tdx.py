"""通达信独立源采集 + 跨源对账的端到端入口。

流程
----
1. 同步数据源注册表（``metadata.data_source``）—— 优先级与"是否独立上游"的
   单一事实来源。
2. 采集通达信官网盘后包到 ``raw.daily_bar``，作为**独立第二源**。
3. 重建 ``market.daily_bar``：此时对账才真正有第二个源可比，
   ``market.daily_bar_source_compare`` 不再是空表。
4. 打印对账结论 —— 一致数、口径差数、真实冲突数。

用法::

    python -m scripts.ingest.collect_and_reconcile_tdx --trade-date 2026-11-10
    python -m scripts.ingest.collect_and_reconcile_tdx --trade-date 2026-11-10 --skip-collect
    python -m scripts.ingest.collect_and_reconcile_tdx --trade-date 2026-11-10 --dry-run

注意：``backend/scripts/ingest/csv/`` 目录名会遮蔽标准库 ``csv``，所以在
``backend`` 目录下**必须**用 ``python -m`` 方式运行本脚本。
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.database.session import engine
from app.services.daily_bar_market_builder import DailyBarMarketBuildService
from app.services.data_source_registry import DataSourceRegistryService
from app.services.tdx_daily_collection import TdxDailyBarCollectionService


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="通达信独立源采集与跨源对账")
    parser.add_argument("--trade-date", required=True, help="交易日 YYYY-MM-DD")
    parser.add_argument(
        "--db",
        default=None,
        help="DuckDB 路径，默认取 settings.metadata_db_path",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="跳过盘后包采集，直接用库里已有的通达信数据重建对账",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将要执行的动作，不写库",
    )
    args = parser.parse_args()

    trade_date = _parse_date(args.trade_date)
    db_path = Path(args.db or settings.metadata_db_path)

    print(f"=== 目标交易日 {trade_date} ===")
    print(f"=== 数据库 {db_path} ===")

    if args.db:
        # An explicit path means the caller wants a different database than
        # the one the session module bound at import time.
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    with Session(bind) as db:
        registry = DataSourceRegistryService()
        if args.dry_run:
            print("[dry-run] 将同步数据源注册表")
        else:
            priorities = registry.sync_catalog(db)
            print(f"[1/3] 数据源注册表已同步: {priorities}")

        if args.skip_collect:
            print("[2/3] 跳过盘后包采集（--skip-collect）")
        elif args.dry_run:
            print("[2/3] 将采集通达信盘后包")
        else:
            collector = TdxDailyBarCollectionService()
            result = collector.collect(db, trade_date=trade_date)
            print(
                f"[2/3] 通达信盘后包已采集: {result.accepted_count} 行入库, "
                f"{result.skipped_count} 行重复跳过 (batch {result.batch_no})"
            )

        if args.dry_run:
            print("[3/3] 将重建 market.daily_bar 并执行跨源对账")
            return 0

        builder = DailyBarMarketBuildService()
        build = builder.build_from_raw(
            db,
            start_date=trade_date,
            end_date=trade_date,
            adjust_type="none",
        )
        print(
            f"[3/3] market.daily_bar 重建: 候选 {build.candidates_count}, "
            f"trusted {build.trusted_count}, suspect {build.suspect_count}, "
            f"rejected {build.rejected_count}, 冲突 {build.conflict_count}"
        )

        rows = db.execute(
            text(
                """
                SELECT source_a, source_b, is_conflict, count(*) AS n
                FROM market.daily_bar_source_compare
                WHERE trade_date = :trade_date
                GROUP BY 1, 2, 3
                ORDER BY 3, 4 DESC
                """
            ),
            {"trade_date": trade_date},
        ).all()
        if not rows:
            print("\n对账表仍为空 —— 该交易日没有第二个源可比")
            return 0

        print("\n=== 跨源对账结论 ===")
        for source_a, source_b, is_conflict, count in rows:
            label = "冲突" if is_conflict else "一致/口径差"
            print(f"  {source_a} vs {source_b}: {label} {count} 组")

        conflicts = db.execute(
            text(
                """
                SELECT symbol, source_a, source_b, close_diff_pct,
                       volume_diff_pct, amount_diff_pct, conflict_reason
                FROM market.daily_bar_source_compare
                WHERE trade_date = :trade_date AND is_conflict
                ORDER BY symbol
                LIMIT 30
                """
            ),
            {"trade_date": trade_date},
        ).all()
        if conflicts:
            print(f"\n=== 真实冲突明细（共 {len(conflicts)} 行，限显 30）===")
            for row in conflicts:
                print(
                    f"  {row[0]} {row[1]} vs {row[2]}: "
                    f"close {row[3]} vol {row[4]} amount {row[5]} -- {row[6]}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
