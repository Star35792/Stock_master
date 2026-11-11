"""以 ORM 元数据为权威，恢复控制面缺失的索引。

为什么需要这个脚本
------------------
DuckDB **不收缩文件**，所以控制面定期用 CTAS 重建表来回收空闲块
（``compact_control_plane.py``）或把数据搬出控制面到 Parquet 温层
（``slim_metadata_db.py``）。**CTAS 重建会丢掉序列、唯一索引和列默认值**——
重建后的表只剩一把主键。

2026-11-11 实测：ORM 声明 78 个索引，控制面实际只有 4 个**列组合**匹配，
**74 个缺失**，其中 **13 个是唯一索引**。唯一索引缺失的直接后果是
``ON CONFLICT (...)`` 目标找不到，upsert 直接抛
``Binder Error: The specified columns as conflict target are not referenced
by a UNIQUE/PRIMARY KEY CONSTRAINT or INDEX``——所有依赖 upsert 的写入路径
（估值快照采集、alternative 快照采集、模拟盘幂等、行业历史区间写入……）
全部处于失效状态。

判据：按**列组合**比对，不按索引名
-----------------------------------
``slim_metadata_db.py`` 重建表时会自建 ``ix_slim_*`` 唯一索引，列组合往往
与 ORM 声明不同（外键列 vs ``idempotency_key``）。DuckDB 的 ``ON CONFLICT``
只认列组合、不认名字，所以**必须按列组合判定**：列组合齐了就算数，名字不同
只是命名不规范，不是缺失。

用法::

    cd backend && python -m scripts.maintenance.restore_indexes            # 体检 + 恢复
    cd backend && python -m scripts.maintenance.restore_indexes --dry-run  # 只体检
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.base import Base  # noqa: E402
from app.database.session import engine  # noqa: E402

# 显式导入所有模型子包，确保 ``Base.metadata`` 里注册了全部表与索引。
# 只 ``import app.models`` 不会触发子模块，metadata 会是空的。
from app.models import (  # noqa: E402,F401
    factor,
    fundamental,
    lake,
    market,
    portfolio,
    raw,
    risk,
    strategy,
    system,
    trade,
)


@dataclass(frozen=True)
class IndexSpec:
    """一条 ORM 声明的索引。"""

    table: str
    name: str
    unique: bool
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ActualIndex:
    """控制面上实际存在的一条索引。"""

    table: str
    name: str
    unique: bool
    columns: tuple[str, ...] | None


def _columns_from_sql(sql: str | None) -> tuple[str, ...] | None:
    if not sql:
        return None
    match = re.search(r"\((.*)\)", sql, re.S)
    if not match:
        return None
    return tuple(part.strip().strip('"') for part in match.group(1).split(","))


def declared_indexes() -> list[IndexSpec]:
    """从 ORM 元数据读出全部声明的索引（权威来源）。"""

    specs: list[IndexSpec] = []
    for table_name, table in Base.metadata.tables.items():
        for index in table.indexes:
            specs.append(
                IndexSpec(
                    table=table_name,
                    name=index.name,
                    unique=bool(index.unique),
                    columns=tuple(column.name for column in index.columns),
                )
            )
    return sorted(specs, key=lambda spec: (spec.table, spec.name))


def actual_indexes(bind) -> list[ActualIndex]:
    """读出控制面上实际存在的索引。"""

    rows: list[ActualIndex] = []
    with Session(bind) as db:
        result = db.execute(
            text(
                "SELECT table_name, index_name, is_unique, sql FROM duckdb_indexes()"
            )
        )
        for table_name, index_name, is_unique, sql in result:
            rows.append(
                ActualIndex(
                    table=table_name,
                    name=index_name,
                    unique=bool(is_unique),
                    columns=_columns_from_sql(sql),
                )
            )
    return rows


def plan_indexes(
    specs: list[IndexSpec], existing: list[ActualIndex]
) -> tuple[list[IndexSpec], list[IndexSpec]]:
    """返回 ``(需要新建, 已有等价)``。

    等价判据 = 同表 + 同列组合 + 同唯一性。索引名不同不影响
    ``ON CONFLICT``，因此不视为缺失。
    """

    by_table: dict[str, list[ActualIndex]] = {}
    for item in existing:
        by_table.setdefault(item.table, []).append(item)

    missing: list[IndexSpec] = []
    covered: list[IndexSpec] = []
    for spec in specs:
        short = spec.table.split(".")[-1]
        candidates = by_table.get(short, [])
        equivalent = any(
            item.unique == spec.unique and item.columns == spec.columns
            for item in candidates
        )
        (covered if equivalent else missing).append(spec)
    return missing, covered


def _table_exists(bind, table_name: str) -> bool:
    inspector = inspect(bind)
    schema, _, name = table_name.partition(".")
    return inspector.has_table(name, schema=schema or None)


def _row_count(bind, table_name: str) -> int:
    with Session(bind) as db:
        return int(db.execute(text(f"SELECT count(*) FROM {table_name}")).scalar_one())


def restore(
    bind,
    specs: list[IndexSpec],
    *,
    dry_run: bool = False,
) -> tuple[int, list[tuple[str, str]], list[str]]:
    """创建缺失索引。返回 ``(成功数, 失败明细, 跳过的表)``。"""

    created = 0
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []
    for spec in specs:
        if not _table_exists(bind, spec.table):
            skipped.append(spec.table)
            continue
        if dry_run:
            continue
        unique = "UNIQUE " if spec.unique else ""
        statement = (
            f"CREATE {unique}INDEX {spec.name} ON {spec.table} "
            f"({', '.join(spec.columns)})"
        )
        try:
            with Session(bind) as db:
                db.execute(text(statement))
                db.commit()
            created += 1
        except Exception as error:  # noqa: BLE001 - 报告后继续
            failures.append((spec.name, str(error).splitlines()[0][:200]))
    return created, failures, sorted(set(skipped))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore missing control-plane indexes from ORM metadata",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="only report what is missing; do not create anything",
    )
    parser.add_argument("--db", help="override the control-plane database path")
    args = parser.parse_args()

    db_path = Path(args.db or settings.metadata_db_path)
    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    specs = declared_indexes()
    existing = actual_indexes(bind)
    missing, covered = plan_indexes(specs, existing)
    unique_missing = [spec for spec in missing if spec.unique]

    print("=== 索引体检 ===")
    print(f"数据库        : {db_path}")
    print(f"ORM 声明索引  : {len(specs)}")
    print(f"实际等价覆盖  : {len(covered)}")
    print(f"缺失          : {len(missing)}（其中唯一索引 {len(unique_missing)}）")

    if unique_missing:
        print("\n缺失的唯一索引（这些直接决定 ON CONFLICT 能否工作）:")
        for spec in unique_missing:
            print(f"  {spec.table:44} {spec.name:44} {list(spec.columns)}")

    if args.dry_run:
        print("\n--dry-run：未做任何修改")
        return 0

    # 建索引前后逐表比对行数，确保没有动到数据行。
    before: dict[str, int] = {}
    for spec in missing:
        if spec.table not in before and _table_exists(bind, spec.table):
            before[spec.table] = _row_count(bind, spec.table)

    created, failures, skipped = restore(bind, missing)
    print("\n=== 恢复结果 ===")
    print(f"新建索引      : {created}/{len(missing)}")
    if skipped:
        print(f"跳过（表不存在）: {', '.join(skipped)}")
    if failures:
        print("失败:")
        for name, message in failures:
            print(f"  {name}: {message}")

    drifted = []
    for table, count in before.items():
        after = _row_count(bind, table)
        if after != count:
            drifted.append((table, count, after))
    if drifted:
        print("\n行数发生变化（异常）:")
        for table, count, after in drifted:
            print(f"  {table}: {count} -> {after}")
    else:
        print(f"行数校验      : {len(before)} 张表，0 差异")

    remaining, _ = plan_indexes(specs, actual_indexes(bind))
    print(f"复查仍缺      : {len(remaining)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
