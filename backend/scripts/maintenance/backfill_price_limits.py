"""控制面 ``market.daily_bar`` 涨跌停回填（P2-1）。

用 ``app.market.price_limit.derive_price_limits``（温层读路径同一实现）
把 ``limit_up`` / ``limit_down`` 持久化进控制面日线：

* 只处理 ``adjust_type='none'``、``limit_up IS NULL``、``pre_close`` 有效的行
  （幂等：重复运行只补新增缺口）。
* ST 判定：优先 ``daily_bar.special_treatment_status``（时点字段，当前仅
  0.61% 覆盖）；否则退回 ``market.security.name`` 的 ST/*ST 前缀——这是
  **当前标签近似**，历史期的 ST 窗口无法从本地数据恢复，主板 ST 的 5%
  带宽在历史上被高估为 10%（偏宽松，方向上不会虚增约束）。
* 指数/ETF（非 stock）与未知板块不填，保持 NULL 语义。
* 结果经临时表一条 UPDATE 写回，适配 DuckDB 的大批量更新路径。

回填后 paper 执行链路的可交易性检查（涨跌停不可买/不可卖）从
「全可交易」变为真实约束；下一次温层导出会自动携带该列。
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sqlalchemy import text  # noqa: E402

from app.database.session import SessionLocal  # noqa: E402
from app.market.price_limit import derive_price_limits  # noqa: E402
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

ST_PREFIXES = ("ST", "*ST", "S*ST", "SST")


def _is_st_by_name(name: str | None) -> bool:
    if not name:
        return False
    normalized = name.strip().upper()
    return any(normalized.startswith(prefix) for prefix in ST_PREFIXES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="控制面日线涨跌停回填")
    parser.add_argument("--dry-run", action="store_true", help="只统计将填充的行数，不写库")
    parser.add_argument("--batch-size", type=int, default=50_000)
    args = parser.parse_args(argv)

    with duckdb_write_lock(timeout_seconds=120):
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
            SELECT b.id, b.symbol, b.pre_close, b.special_treatment_status,
                   s.security_type, s.name
            FROM market.daily_bar b
            LEFT JOIN market.security s ON s.symbol = b.symbol
            WHERE b.adjust_type = 'none'
              AND b.limit_up IS NULL
              AND b.pre_close IS NOT NULL
              AND b.pre_close > 0
            ORDER BY b.id
        """)
        ).all()
        print(f"待处理行（limit_up 为空且 pre_close 有效）: {len(rows)}")

        updates: list[tuple[int, Decimal, Decimal]] = []
        skipped = {"non_stock": 0, "no_band": 0, "derive_none": 0}
        for row_id, symbol, pre_close, st_status, security_type, name in rows:
            if security_type is not None and security_type != "stock":
                skipped["non_stock"] += 1
                continue
            st_flag = (st_status or "").strip() or ("ST" if _is_st_by_name(name) else None)
            limit_up, limit_down = derive_price_limits(
                pre_close,
                symbol=symbol,
                security_type=security_type,
                special_treatment_status=st_flag,
            )
            if limit_up is None or limit_down is None:
                skipped["no_band"] += 1
                continue
            updates.append((row_id, limit_up, limit_down))

        print(f"将填充: {len(updates)} 行；跳过分布: {skipped}")
        if args.dry_run or not updates:
            return 0

        db.execute(
            text(
                "CREATE OR REPLACE TEMP TABLE _tmp_limits AS "
                "SELECT NULL::BIGINT AS id, NULL::DECIMAL(18,4) AS limit_up, "
                "NULL::DECIMAL(18,4) AS limit_down WHERE 1=0"
            )
        )
        total = 0
        for start in range(0, len(updates), args.batch_size):
            chunk = updates[start : start + args.batch_size]
            db.execute(
                text(
                    "INSERT INTO _tmp_limits (id, limit_up, limit_down) "
                    "SELECT unnest(:ids)::BIGINT, unnest(:ups)::DECIMAL(18,4), "
                    "unnest(:downs)::DECIMAL(18,4)"
                ),
                {
                    "ids": [item[0] for item in chunk],
                    "ups": [item[1] for item in chunk],
                    "downs": [item[2] for item in chunk],
                },
            )
            total += len(chunk)
            print(f"  已暂存 {total}/{len(updates)}")
        updated = db.execute(
            text("""
            UPDATE market.daily_bar b
            SET limit_up = t.limit_up, limit_down = t.limit_down
            FROM _tmp_limits t
            WHERE b.id = t.id
        """)
        ).rowcount
        db.commit()
        print(f"已更新 {updated} 行（adjust_type='none'，幂等：仅原 NULL 行）")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
