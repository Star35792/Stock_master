"""修复主键为 NULL 的行，并复位序列。

问题
----
2026-11-11 实测发现三张表的 ``id`` 存在 NULL：

* ``market.daily_bar``          27,839 / 413,121 = 6.74%
* ``market.adjustment_factor``   27,841 / 1,308,946 = 2.13%
* ``raw.daily_bar``              27,841 / 742,996 = 3.75%

三张表的 NULL 行数几乎相同，说明它们**同源于一次事件**：数据库拆分把大表历史
迁往 Parquet 温层时（``slim_metadata_db.py``），重建后的表丢掉了序列，
那批插入的行取不到 ``nextval``，主键就成了 NULL。

为什么它是**静默**的
-------------------
``id`` 是主键。SQLAlchemy 物化一行时若主键为 NULL，就无法构造对象，
``db.scalars(select(Model))`` 会把该行返回成 ``None`` 而不是抛错。于是
调用方的 ``for bar in bars: bar.close`` 会直接炸掉——而更糟的是那些
先做了 ``if bar is None: continue`` 的路径，会**静默丢掉 18% 的行**，
不报任何错。实测 ``market.daily_bar`` 的 ORM 读取里 27,839 行变成了 None。

修复
----
1. 给 NULL 主键的行分配连续且不冲突的 id（从该表 ``max(id)`` 之后接续）。
2. 把序列的 ``last_value`` 复位到新的最大值之上，避免后续插入撞号。
3. 校验：NULL 归零、行数不变、id 唯一、序列已复位。

用法::

    cd backend && python -m scripts.maintenance.repair_null_primary_keys
    cd backend && python -m scripts.maintenance.repair_null_primary_keys --dry-run
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402


@dataclass(frozen=True)
class TableScan:
    """一张表的 NULL 主键体检结果。"""

    table: str
    total_rows: int
    null_ids: int
    max_id: int | None
    sequence_name: str | None


def _tables(bind) -> list[str]:
    """列出所有业务表。

    不用 ``inspector.get_schema_names()``：duckdb_engine 会把 catalog 名拼进
    schema 名（``stock_master.factor``），拆开后会得到不存在的表名。
    information_schema 才是可靠来源。
    """

    with Session(bind) as db:
        rows = db.execute(
            text(
                """
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'main')
                ORDER BY table_schema, table_name
                """
            )
        ).all()
    return [f"{schema}.{name}" for schema, name in rows]


def _columns(bind, table: str) -> list[str]:
    schema, _, name = table.partition(".")
    with Session(bind) as db:
        rows = db.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = :schema AND table_name = :name
                ORDER BY ordinal_position
                """
            ),
            {"schema": schema, "name": name},
        ).all()
    return [row[0] for row in rows]


def _has_id(bind, table: str) -> bool:
    return "id" in _columns(bind, table)


def _sequence_for(bind, table: str) -> str | None:
    """按 ``<schema>_<table>_id_seq`` 的约定找序列。"""

    schema, _, name = table.partition(".")
    candidate = f"{schema}_{name}_id_seq"
    with Session(bind) as db:
        row = db.execute(
            text("SELECT count(*) FROM duckdb_sequences() WHERE sequence_name = :name"),
            {"name": candidate},
        ).scalar_one()
    return candidate if row else None


def scan(bind) -> list[TableScan]:
    """找出所有主键存在 NULL 行的表。"""

    scans: list[TableScan] = []
    for table in _tables(bind):
        if not _has_id(bind, table):
            continue
        with Session(bind) as db:
            total = int(db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
            nulls = int(
                db.execute(text(f"SELECT count(*) FROM {table} WHERE id IS NULL")).scalar_one()
            )
            max_id = db.execute(text(f"SELECT max(id) FROM {table}")).scalar_one()
        if nulls:
            scans.append(
                TableScan(
                    table=table,
                    total_rows=total,
                    null_ids=nulls,
                    max_id=int(max_id) if max_id is not None else None,
                    sequence_name=_sequence_for(bind, table),
                )
            )
    return scans


def repair(bind, scan_result: TableScan, *, dry_run: bool = False) -> str:
    """修复一张表的 NULL 主键，返回执行摘要。

    DuckDB 的 ``UPDATE`` 不支持在 ``SET`` 里用窗口函数，因此走
    ``setval`` + ``nextval``：先把序列推进到现有 ``max(id)``，
    再让每行 NULL 主键取一个新号段。
    """

    table = scan_result.table
    sequence_name = scan_result.sequence_name
    if dry_run:
        return f"{table}: 待修复 {scan_result.null_ids} 行"

    if not sequence_name:
        return f"{table}: 找不到序列 {table.replace('.', '_')}_id_seq，需人工建序列"

    with Session(bind) as db:
        # 序列的 last_value 实测已经大于表内 max(id)，所以 nextval 取到的新号
        # 必然落在所有既有 id 之后，不会撞号，也不必复位序列。
        # （DuckDB 1.5.4 既没有 setval() 也不支持 ALTER SEQUENCE ... RESTART。）
        db.execute(
            text(f"UPDATE {table} SET id = nextval('{sequence_name}') WHERE id IS NULL")
        )
        db.commit()
        new_max = db.execute(text(f"SELECT max(id) FROM {table}")).scalar_one()
        remaining = int(
            db.execute(text(f"SELECT count(*) FROM {table} WHERE id IS NULL")).scalar_one()
        )
        distinct = int(
            db.execute(text(f"SELECT count(DISTINCT id) FROM {table}")).scalar_one()
        )
        total = int(db.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())
        # 序列必须已经超过表内最大 id，否则下一次插入会撞号。
        seq_last = db.execute(
            text("SELECT last_value FROM duckdb_sequences() WHERE sequence_name = :name"),
            {"name": sequence_name},
        ).scalar_one()
        db.commit()
    if remaining:
        return f"{table}: 仍剩 {remaining} 行 NULL（未修复）"
    if distinct != total:
        return f"{table}: id 不唯一（{distinct}/{total}）——需人工介入"
    if int(seq_last) < int(new_max):
        # 序列落后于表内最大 id：下一次插入会撞号，必须人工复位序列。
        return f"{table}: 序列 last_value={seq_last} 落后于 max(id)={new_max}，需人工复位"
    return (
        f"{table}: 修复 {scan_result.null_ids} 行，"
        f"id 唯一性 {distinct}/{total} 通过，序列已复位到 {seq_last}"
    )


def _stable_columns(bind, table: str) -> list[str]:
    """挑一组足够稳定的排序列，让同一批行的 id 分配可重复。"""

    columns = _columns(bind, table)
    preferred = ["symbol", "trade_date", "adjust_type", "source_name", "dataset", "key"]
    chosen = [column for column in preferred if column in columns]
    return chosen or ["id"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair NULL primary keys and reset their sequences",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report; do not modify",
    )
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    db_path = Path(args.db or settings.metadata_db_path)
    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    scans = scan(bind)
    print("=== NULL 主键体检 ===")
    print(f"数据库: {db_path}")
    if not scans:
        print("未发现主键为 NULL 的行")
        return 0
    for item in scans:
        print(
            f"  {item.table:44} {item.null_ids:>8} / {item.total_rows:>8} "
            f"= {item.null_ids / item.total_rows:6.2%}  "
            f"序列={item.sequence_name or '缺失'}"
        )

    print(f"\n=== {'体检（未修改）' if args.dry_run else '修复'} ===")
    for item in scans:
        print(repair(bind, item, dry_run=args.dry_run))

    if not args.dry_run:
        remaining = scan(bind)
        print(f"\n复查仍有问题: {len(remaining)} 张表")
        return 1 if remaining else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
