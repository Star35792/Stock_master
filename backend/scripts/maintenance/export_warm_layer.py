"""把 DuckDB 中体量最大的分析表导出为分区 Parquet 温层。

这是“把 stock_master.duckdb 拆成多个文件”的第一阶段：
- 导出：market.daily_bar、raw.daily_bar、market.adjustment_factor、factor.factor_value
- 按 trade_year/trade_month 分区，便于按时间片读取
- 生成 manifest（行数、checksum、schema、日期范围）
- 校验导出前后行数一致

注意：本脚本**只读**源库并写新 Parquet，不删除源表数据；真正的
DuckDB→Parquet 读切与写切需要后续阶段配合 repository/service 改造。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = BACKEND_DIR / "data" / "stock_master.duckdb"
DEFAULT_WARM_ROOT = BACKEND_DIR / "data" / "warm" / "exported"

EXPORT_TABLES = [
    ("market", "daily_bar", "trade_date"),
    ("raw", "daily_bar", "trade_date"),
    ("market", "adjustment_factor", "trade_date"),
    ("factor", "factor_value", "trade_date"),
    ("raw", "corporate_action", "ex_date"),
    ("fundamental", "financial_statement", "report_date"),
    ("raw", "financial_payload", "report_date"),
]


@dataclass
class ExportResult:
    schema: str
    table: str
    row_count: int
    min_date: datetime.date | None
    max_date: datetime.date | None
    output_dir: Path
    manifest_path: Path
    files: list[Path]


def _table_fingerprint(
    con: duckdb.DuckDBPyConnection,
    schema: str,
    table: str,
    date_column: str,
) -> dict[str, Any]:
    row = con.execute(
        f"""
        SELECT COUNT(*), MIN({date_column}), MAX({date_column})
        FROM {schema}.{table}
        """
    ).fetchone()
    return {
        "row_count": int(row[0]),
        "min_date": str(row[1]) if row[1] else None,
        "max_date": str(row[2]) if row[2] else None,
    }


def _file_checksum(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def export_table(
    con: duckdb.DuckDBPyConnection,
    *,
    schema: str,
    table: str,
    date_column: str,
    warm_root: Path,
    release_tag: str,
) -> ExportResult:
    """把一张表按年月分区导出为 Parquet，并写 manifest。"""
    output_dir = warm_root / release_tag / schema / table
    if output_dir.exists():
        raise FileExistsError(f"release table already exists; use a new release tag: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)

    fingerprint = _table_fingerprint(con, schema, table, date_column)
    logger.info(
        "导出 %s.%s: %s 行, %s ~ %s",
        schema,
        table,
        fingerprint["row_count"],
        fingerprint["min_date"],
        fingerprint["max_date"],
    )

    # DuckDB COPY with Hive partitioning.
    # Use row_group_size ~ 100k for good compression and read granularity.
    copy_sql = f"""
        COPY (
            SELECT *,
                   year({date_column}) AS trade_year,
                   month({date_column}) AS trade_month
            FROM {schema}.{table}
        )
        TO '{output_dir.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (trade_year, trade_month),
         OVERWRITE_OR_IGNORE true, ROW_GROUP_SIZE 100000,
         COMPRESSION 'ZSTD')
    """
    con.execute(copy_sql)

    files = sorted(output_dir.rglob("*.parquet"))
    logger.info("%s.%s 已写入 %s 个 parquet 文件到 %s", schema, table, len(files), output_dir)

    file_manifest = [
        {
            "path": str(file.relative_to(warm_root)),
            "size": file.stat().st_size,
            "sha256": _file_checksum(file),
        }
        for file in files
    ]

    manifest = {
        "schema": schema,
        "table": table,
        "release_tag": release_tag,
        "exported_at": datetime.now(UTC).isoformat(),
        "source_db": str(DEFAULT_DB_PATH.resolve()),
        **fingerprint,
        "partition_columns": ["trade_year", "trade_month"],
        "files": file_manifest,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("manifest 已写入 %s", manifest_path)

    return ExportResult(
        schema=schema,
        table=table,
        row_count=fingerprint["row_count"],
        min_date=fingerprint["min_date"],
        max_date=fingerprint["max_date"],
        output_dir=output_dir,
        manifest_path=manifest_path,
        files=files,
    )


def verify_export(
    con: duckdb.DuckDBPyConnection,
    result: ExportResult,
) -> bool:
    """通过 DuckDB 读取导出的 Parquet 并核对总行数。"""
    logger.info("校验 %s.%s ...", result.schema, result.table)
    parquet_count = con.execute(
        f"""
        SELECT COUNT(*)
        FROM read_parquet('{result.output_dir.as_posix()}/**/*.parquet')
        """
    ).fetchone()[0]
    source_count = con.execute(f"SELECT COUNT(*) FROM {result.schema}.{result.table}").fetchone()[0]
    ok = int(parquet_count) == int(source_count)
    if ok:
        logger.info(
            "校验通过: %s.%s 源表 %s 行 == Parquet %s 行",
            result.schema,
            result.table,
            source_count,
            parquet_count,
        )
    else:
        logger.error(
            "校验失败: %s.%s 源表 %s 行 != Parquet %s 行",
            result.schema,
            result.table,
            source_count,
            parquet_count,
        )
    return ok


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="导出 DuckDB 大表到 Parquet 温层")
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help="源 DuckDB 路径",
    )
    parser.add_argument(
        "--warm-root",
        default=str(DEFAULT_WARM_ROOT),
        help="Parquet 温层输出根目录",
    )
    parser.add_argument(
        "--release-tag",
        default=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        help="本次导出 release tag",
    )
    parser.add_argument(
        "--base-release-tag",
        help="可选父 release；用于把控制面热窗口发布为可继承历史的 partial release",
    )
    parser.add_argument(
        "--snapshot-table",
        action="append",
        default=[],
        metavar="SCHEMA.TABLE",
        help=(
            "父 release 存在时，显式声明本次完整覆盖父层的表；"
            "未声明的已导出表按 delta 与父层合并。可重复传入。"
        ),
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="跳过 Parquet 行数校验",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = Path(args.db_path)
    warm_root = Path(args.warm_root)
    release_tag = args.release_tag
    snapshot_tables = set(args.snapshot_table)

    if not db_path.exists():
        logger.error("数据库不存在: %s", db_path)
        return 1

    warm_root.mkdir(parents=True, exist_ok=True)

    results: list[ExportResult] = []
    all_ok = True

    with duckdb.connect(str(db_path), read_only=True) as con:
        for schema, table, date_column in EXPORT_TABLES:
            try:
                result = export_table(
                    con,
                    schema=schema,
                    table=table,
                    date_column=date_column,
                    warm_root=warm_root,
                    release_tag=release_tag,
                )
                results.append(result)
                if not args.skip_verify:
                    if not verify_export(con, result):
                        all_ok = False
            except Exception:
                logger.exception("导出 %s.%s 失败", schema, table)
                all_ok = False

    summary = {
        "release_tag": release_tag,
        "base_release_tag": args.base_release_tag,
        "partial_release": bool(args.base_release_tag),
        "db_path": str(db_path.resolve()),
        "warm_root": str(warm_root.resolve()),
        "tables": [
            {
                "schema": r.schema,
                "table": r.table,
                "mode": (
                    "snapshot"
                    if not args.base_release_tag or f"{r.schema}.{r.table}" in snapshot_tables
                    else "delta"
                ),
                "row_count": r.row_count,
                "min_date": str(r.min_date) if r.min_date else None,
                "max_date": str(r.max_date) if r.max_date else None,
                "output_dir": str(r.output_dir.relative_to(warm_root.parent.parent)),
                "manifest": str(r.manifest_path.relative_to(warm_root.parent.parent)),
            }
            for r in results
        ],
        "all_ok": all_ok,
    }
    summary_path = warm_root / release_tag / "export_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("汇总已写入 %s", summary_path)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
