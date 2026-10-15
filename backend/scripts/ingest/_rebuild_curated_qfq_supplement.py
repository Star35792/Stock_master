"""增量补齐后重建 curated 前复权温层。

等价于 ``CuratedLayer.build_prices_qfq_from_db`` 的 COPY 部分，但：
- 跳过 ``_compact_partitions``（避免逐分区 unlink 触发环境批量删除保护钩子）；
- 用 ``mv`` 改名提升，旧活动层保留为 ``prices_qfq.prev-<stamp>``，不删除。
未压缩分区（每分区 1 个文件）对 ``CuratedDailyBarReader`` 读取无正确性问题。
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb


def security_type_case_sql(symbol_expr: str = "symbol") -> str:
    code = f"split_part({symbol_expr}, '.', 1)"
    exchange = f"split_part({symbol_expr}, '.', 2)"
    return f"""
        CASE
            WHEN {code}[1:2] IN ('15','16','18','51','56','58') THEN 'etf'
            WHEN {code}[1:3] = '399'
                 OR ({exchange} = 'SH' AND {code}[1:3] = '000') THEN 'index'
            WHEN {code}[1:2] IN ('00','30','60','68','83','87','92') THEN 'stock'
            ELSE 'unknown'
        END
    """


def main() -> None:
    root = Path("data/warm/curated")
    db_path = "data/stock_master.duckdb"
    target = root / "prices_qfq"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    temp_target = root / f".prices_qfq.tmp-{stamp}-{uuid.uuid4().hex[:8]}"
    temp_target.mkdir(parents=True, exist_ok=False)

    trusted_filter = "d.quality_status = 'trusted' AND d.adjust_type = 'qfq'"
    sql = f"""
        COPY (
            SELECT
                d.symbol,
                {security_type_case_sql("d.symbol")} AS security_type,
                d.trade_date,
                d.open, d.high, d.low, d.close,
                d.volume, d.amount,
                d.turnover_rate,
                COALESCE(f.factor, CAST(1 AS DECIMAL(24,10))) AS qfq_factor,
                year(d.trade_date) AS year, month(d.trade_date) AS month
            FROM market.daily_bar d
            LEFT JOIN LATERAL (
                SELECT af.factor
                FROM market.adjustment_factor af
                WHERE af.symbol = d.symbol
                  AND af.trade_date = d.trade_date
                  AND af.adjust_type = 'qfq'
                  AND af.source_name = 'daily_loop'
                ORDER BY af.updated_at DESC, af.id DESC
                LIMIT 1
            ) f ON true
            WHERE {trusted_filter}
            ORDER BY d.symbol, d.trade_date
        ) TO ? (
            FORMAT PARQUET, COMPRESSION ZSTD,
            PARTITION_BY (year, month), PER_THREAD_OUTPUT false,
            ROW_GROUP_SIZE 100000
        )
    """

    con = duckdb.connect(db_path)
    try:
        con.execute("SET threads = 1")
        stats = con.execute(
            "SELECT count(*), min(trade_date), max(trade_date) "
            "FROM market.daily_bar WHERE quality_status = 'trusted' AND adjust_type = 'qfq'"
        ).fetchone()
        print(f"source_rows={stats[0]} min_date={stats[1]} max_date={stats[2]}", flush=True)
        con.execute(sql, [str(temp_target)])
    finally:
        con.close()

    n_files = sum(1 for _ in temp_target.rglob("*.parquet"))
    print(f"wrote temp={temp_target} parquet_files={n_files}", flush=True)

    # 改名提升：旧层保留为 prev，不删除
    if target.exists():
        prev = root / f"prices_qfq.prev-{stamp}"
        shutil.move(str(target), str(prev))
        print(f"moved old active -> {prev}", flush=True)
    shutil.move(str(temp_target), str(target))
    print(f"promoted temp -> {target}", flush=True)
    print("done")


if __name__ == "__main__":
    main()
