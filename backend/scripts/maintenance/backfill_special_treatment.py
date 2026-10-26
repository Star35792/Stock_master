"""控制面 ``market.daily_bar.special_treatment_status`` 回填。

该字段被价格限价推导、选股初筛与模拟执行链路读取，但**没有任何日线数据源
写入过它**——字段存在于 ``raw`` 与 ``market`` 两层模型，却始终为 NULL。
初筛把「未知」当作「受限」，于是每一个候选都被
``treatment_unknown_or_restricted`` 阻断（连续 83 次运行 0 可复核候选）。

本脚本用证券主数据的当前 ST 状态回填：

* 状态来源是 ``market.security.special_treatment_status``，由
  ``app.market.special_treatment.derive_special_treatment_status`` 从证券简称
  派生（交易所给 ST/*ST 股票改名，简称即当日状态的直接证据）。
* **只回填控制面热窗口**，即日线同步实际覆盖的近期区间。历史期的 ST 窗口
  无法从本地数据恢复：供应商在所有历史行上返回的是**当前**简称，把它
  倒填到历史会制造未来函数，而不是补齐缺口。历史期的 ST 状态继续挂账，
  并在研究结果中显式声明。
* 只处理 ``adjust_type='none'`` 且该字段为 NULL 的行（幂等：重复运行只补
  新增缺口）。
* 退市整理期证券（简称含「退」）写 ``delisting``，不写 ``ST``：它们由
  可交易性判定处理，不走 +/-5% 带宽。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.database.session import SessionLocal, initialize_database  # noqa: E402
from app.market.special_treatment import (  # noqa: E402
    derive_special_treatment_status,
)
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start-date",
        default=None,
        help="Only backfill bars on or after this date (ISO).",
    )
    parser.add_argument(
        "--end-date",
        default=None,
        help="Only backfill bars on or before this date (ISO).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be written without writing it.",
    )
    args = parser.parse_args()

    initialize_database()

    conditions = [
        "b.adjust_type = 'none'",
        "b.special_treatment_status IS NULL",
        "s.special_treatment_status IS NOT NULL",
    ]
    params: dict[str, object] = {}
    if args.start_date:
        conditions.append("b.trade_date >= :start_date")
        params["start_date"] = args.start_date
    if args.end_date:
        conditions.append("b.trade_date <= :end_date")
        params["end_date"] = args.end_date
    where = " AND ".join(conditions)

    with duckdb_write_lock("market"):
        with SessionLocal() as db:
            # Step 1: derive the status from the security master names.
            # The security rows predate the column, so the status has to be
            # derived once before it can be propagated to the bars.
            #
            # Only listed companies carry an ST marker.  The security master
            # also holds concept and sector indices whose names contain the
            # word (``ST板块`` is a sector index, not a special-treated
            # stock), so the derivation is restricted to ``stock`` rows.
            securities = db.execute(
                text(
                    "SELECT id, name FROM market.security "
                    "WHERE security_type = 'stock'"
                )
            ).fetchall()
            derived: list[tuple[str, int]] = []
            for security_id, name in securities:
                status = derive_special_treatment_status(name)
                if status is None:
                    continue
                derived.append((status, security_id))
            if not args.dry_run and derived:
                db.execute(
                    text(
                        "UPDATE market.security SET special_treatment_status = :status "
                        "WHERE id = :id"
                    ),
                    [{"status": status, "id": security_id} for status, security_id in derived],
                )
                db.commit()

            before = db.execute(
                text(
                    "SELECT count(*) FROM market.daily_bar "
                    "WHERE special_treatment_status IS NOT NULL"
                )
            ).scalar_one()
            eligible = db.execute(
                text(
                    "SELECT count(*) FROM market.daily_bar b "
                    "JOIN market.security s ON s.symbol = b.symbol "
                    f"WHERE {where}"
                ),
                params,
            ).scalar_one()
            if not args.dry_run:
                db.execute(
                    text(
                        "UPDATE market.daily_bar b "
                        "SET special_treatment_status = s.special_treatment_status "
                        "FROM market.security s "
                        "WHERE s.symbol = b.symbol "
                        f"AND {where}"
                    ),
                    params,
                )
                db.commit()
            after = db.execute(
                text(
                    "SELECT count(*) FROM market.daily_bar "
                    "WHERE special_treatment_status IS NOT NULL"
                )
            ).scalar_one()

            breakdown = db.execute(
                text(
                    "SELECT b.special_treatment_status, count(*) "
                    "FROM market.daily_bar b "
                    "WHERE b.special_treatment_status IS NOT NULL "
                    "GROUP BY 1 ORDER BY 2 DESC"
                )
            ).fetchall()

            security_breakdown = db.execute(
                text(
                    "SELECT special_treatment_status, count(*) "
                    "FROM market.security "
                    "WHERE special_treatment_status IS NOT NULL "
                    "GROUP BY 1 ORDER BY 2 DESC"
                )
            ).fetchall()

    print(f"security_statuses_derived={len(derived)}")
    for status, count in security_breakdown:
        print(f"  security.{status}: {count}")
    print(f"eligible_rows={eligible}")
    print(f"non_null_before={before}")
    print(f"non_null_after={after}")
    for status, count in breakdown:
        print(f"  {status}: {count}")
    if args.dry_run:
        print("dry_run=True (no rows written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
