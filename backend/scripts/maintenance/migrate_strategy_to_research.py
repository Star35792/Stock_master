"""把主库 ``strategy`` 回测表迁移到独立 research.duckdb（一次性）。

拆库背景见 ADR ``aiDoc/relations/20261024-*-research-database-split.md``。
步骤（幂等，可安全重跑）：

1. 以 ORM 在 ``data/research.duckdb`` 建好带主键/约束/sequence 的空表
   （``initialize_research_database``）。
2. 逐表 ``INSERT INTO research.strategy.<t> SELECT * FROM strategy.<t>``
   （ATTACH 同一连接，单进程持有两个文件，无并发冲突）。
3. 行数核对一致后，把 sequence 重启到 max(id)+1，再 DROP 主库旧表。
   （``--keep-main`` 只复制不删除，用于先观察。）

运行期间持有主库与研究库两把文件锁。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import duckdb  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.database.research_session import (  # noqa: E402
    initialize_research_database,
)
from app.utils.duckdb_write_lock import duckdb_write_lock  # noqa: E402

TABLES = (
    "training_artifact",
    "backtest_run",
    "backtest_trade",
    "backtest_equity",
    "backtest_position",
    "backtest_performance",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="strategy 表迁移到 research.duckdb")
    parser.add_argument("--keep-main", action="store_true", help="只复制，不删除主库旧表")
    args = parser.parse_args(argv)

    initialize_research_database()

    with duckdb_write_lock("data/duckdb_write.lock", timeout_seconds=120):
        with duckdb_write_lock("data/research_write.lock", timeout_seconds=120):
            con = duckdb.connect(settings.metadata_db_path)
            con.execute(f"ATTACH '{settings.research_db_path}' AS research")
            try:
                for table in TABLES:
                    copied = con.execute(
                        f"SELECT count(*) FROM research.strategy.{table}"
                    ).fetchone()[0]
                    source = con.execute(f"SELECT count(*) FROM strategy.{table}").fetchone()[0]
                    if copied == source and source > 0:
                        print(f"  {table}: 已同步 {source} 行，跳过复制")
                        if not args.keep_main:
                            con.execute(f"DROP TABLE strategy.{table}")
                            print(f"  {table}: 主库旧表已删除")
                        continue
                    if copied:
                        raise SystemExit(
                            f"{table}: research 已有 {copied} 行与主库 {source} 行不一致，"
                            "请人工核对后处理"
                        )
                    # Legacy tables gained columns via ALTER (appended at the
                    # end), so the physical column order differs from the ORM
                    # table; select by the target's column names explicitly.
                    columns = [
                        row[0]
                        for row in con.execute(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_catalog = 'research' "
                            "AND table_schema = 'strategy' "
                            f"AND table_name = '{table}' ORDER BY ordinal_position"
                        ).fetchall()
                    ]
                    main_catalog = con.execute("SELECT current_database()").fetchone()[0]
                    missing = [
                        column
                        for column in columns
                        if column
                        not in {
                            row[0]
                            for row in con.execute(
                                "SELECT column_name FROM information_schema.columns "
                                "WHERE table_catalog = ? "
                                "AND table_schema = 'strategy' "
                                f"AND table_name = '{table}'",
                                [main_catalog],
                            ).fetchall()
                        }
                    ]
                    if missing:
                        raise SystemExit(f"{table}: 主库缺少列 {missing}，中止")
                    column_list = ", ".join(f'"{column}"' for column in columns)
                    con.execute(
                        f"INSERT INTO research.strategy.{table} ({column_list}) "
                        f"SELECT {column_list} FROM strategy.{table}"
                    )
                    moved = con.execute(
                        f"SELECT count(*) FROM research.strategy.{table}"
                    ).fetchone()[0]
                    if moved != source:
                        raise SystemExit(f"{table}: 复制后 {moved} != 源 {source}，中止")
                    max_id = con.execute(
                        f"SELECT coalesce(max(id), 0) FROM research.strategy.{table}"
                    ).fetchone()[0]
                    if max_id:
                        # ORM sequences live in the database default schema
                        # (``nextval`` is emitted without a schema prefix), so
                        # restart that one; drop any stray schema-qualified
                        # twin. DuckDB has no ALTER SEQUENCE RESTART.
                        con.execute(
                            f"DROP SEQUENCE IF EXISTS research.strategy.strategy_{table}_id_seq"
                        )
                        con.execute(f"DROP SEQUENCE IF EXISTS research.strategy_{table}_id_seq")
                        con.execute(
                            f"CREATE SEQUENCE research.strategy_{table}_id_seq START {max_id + 1}"
                        )
                    print(f"  {table}: 复制 {moved} 行，sequence 重启到 {max_id + 1}")
                    if not args.keep_main:
                        con.execute(f"DROP TABLE strategy.{table}")
                        print(f"  {table}: 主库旧表已删除")
                con.execute("DETACH research")
                con.commit()
            finally:
                con.close()
    print("迁移完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
