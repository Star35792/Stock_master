"""清除用「当前标签回填」算出的行业因子值。

背景
----
``market.security_industry_history`` 在 2026-11-11 之前只有「当前标签回填」
行：每标的 1 条、``effective_from`` = 上市日、``effective_to`` 全 NULL。
这等价于**假设行业从未变更**，于是所有按目标交易日做 as-of 匹配的行业读取
拿到的都是今天的标签。

受影响的因子值
--------------
* ``industry_momentum_60`` / ``industry_breadth_20`` /
  ``industry_turnover_share_change_20``：行业分组来自历史表 → 全被污染。
* ``industry_valuation_gap``：行业分组来自 ``market.security.industry``
  → 全被污染。

实测规模 315,453 行（301,785 + 13,668），全部是 ``factor_version='v1'``。

本脚本只做两件事
----------------
1. 把待删除的行导出成 Parquet 备份（可离线复查）；
2. 删除它们。

删除后必须重算，否则因子池会缺成员。重算命令见文件末尾的说明。
``--dry-run`` 只报告影响范围。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402

#: 受污染的因子名。这些因子的行业分组都来自「当前标签」而非时点行业事实。
CONTAMINATED_FACTORS = (
    "industry_momentum_60",
    "industry_breadth_20",
    "industry_turnover_share_change_20",
    "industry_valuation_gap",
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear industry factor values computed with backfilled labels",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report the affected rows without deleting anything",
    )
    parser.add_argument(
        "--backup",
        help="parquet path for the removed rows (default: data/backups/…)",
    )
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    db_path = Path(args.db or settings.metadata_db_path)
    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    backup_path = Path(
        args.backup
        or Path("data") / "backups" / "industry_factor_contamination_20261111.parquet"
    )

    names = ", ".join(f"'{name}'" for name in CONTAMINATED_FACTORS)
    print("=== 清除被污染的行业因子值 ===")
    print(f"数据库  : {db_path}")
    print(f"因子    : {', '.join(CONTAMINATED_FACTORS)}")
    print(f"备份    : {backup_path}")

    with Session(bind) as db:
        before = db.execute(
            text(
                "SELECT factor_name, count(*) FROM factor.factor_value "
                f"WHERE factor_name IN ({names}) GROUP BY 1 ORDER BY 1"
            )
        ).all()
        total = sum(row[1] for row in before)
        print(f"\n待清除行数: {total}")
        for name, count in before:
            print(f"  {name}: {count}")

        if args.dry_run:
            print("\n--dry-run：未做任何修改")
            return 0

        backup_path.parent.mkdir(parents=True, exist_ok=True)
        db.execute(
            text(
                "COPY (SELECT * FROM factor.factor_value "
                f"WHERE factor_name IN ({names})) "
                f"TO '{backup_path.as_posix()}' (FORMAT PARQUET)"
            )
        )
        print(f"\n备份已写入: {backup_path}")

        result = db.execute(
            text(
                "DELETE FROM factor.factor_value "
                f"WHERE factor_name IN ({names})"
            )
        )
        db.commit()
        print(f"已删除行数: {result.rowcount}")

        remaining = db.execute(
            text(
                "SELECT count(*) FROM factor.factor_value "
                f"WHERE factor_name IN ({names})"
            )
        ).scalar()
        print(f"剩余行数  : {remaining}")

    print(
        "\n重算命令：\n"
        "  python -m scripts.factors.run_industry_factors "
        "--start-date 2026-09-30 --end-date 2026-11-11 --adjust-type none\n"
        "  python -m scripts.factors.run_valuation_factors "
        "--factors industry_valuation_gap "
        "--start-date 2026-09-30 --end-date 2026-11-11 --adjust-type none"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
