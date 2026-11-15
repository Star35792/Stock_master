from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from json import dumps, loads
from pathlib import Path
from shutil import rmtree
from typing import Any
from uuid import uuid4

import duckdb

from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LakeDatasetVersion:
    dataset: str
    release: str
    path: str
    source_path: str
    source_sha256: str
    row_count: int
    min_date: str | None
    max_date: str | None
    created_at: str


class DataLake:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.data_lake_root)
        self.manifest_path = self.root / "manifest.json"

    def import_daily_bars(
        self,
        source: str | Path,
        *,
        release: str | None = None,
    ) -> LakeDatasetVersion:
        source_path = Path(source).resolve()
        release_name = release or _release_name(source_path)
        target = self.root / "daily_bar" / f"release={release_name}"
        if target.exists():
            return self._existing_version("daily_bar", release_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect()
        try:
            connection.execute("SET threads = 1")
            stats = connection.execute(
                """
                SELECT count(*), min(epoch_ms(date_ms + 28800000)::DATE),
                       max(epoch_ms(date_ms + 28800000)::DATE)
                FROM read_parquet(?)
                WHERE adjusted = 'none'
                """,
                [str(source_path)],
            ).fetchone()
            connection.execute(
                """
                COPY (
                    SELECT
                        thscode AS symbol,
                        epoch_ms(date_ms + 28800000)::DATE AS trade_date,
                        open_price AS open,
                        high_price AS high,
                        low_price AS low,
                        close_price AS close,
                        volume,
                        turnover AS amount,
                        currency,
                        year(epoch_ms(date_ms + 28800000)::DATE) AS year,
                        month(epoch_ms(date_ms + 28800000)::DATE) AS month
                    FROM read_parquet(?)
                    WHERE adjusted = 'none'
                ) TO ? (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    PARTITION_BY (year, month),
                    PER_THREAD_OUTPUT false,
                    ROW_GROUP_SIZE 100000
                )
                """,
                [str(target), str(source_path)],
            )
        finally:
            connection.close()
        _compact_partitions(target)
        version = _version("daily_bar", release_name, target, source_path, stats)
        self._register(version)
        return version

    def import_corporate_actions(
        self,
        source: str | Path,
        *,
        release: str | None = None,
    ) -> LakeDatasetVersion:
        source_path = Path(source).resolve()
        release_name = release or _release_name(source_path)
        target = self.root / "corporate_action" / f"release={release_name}"
        if target.exists():
            return self._existing_version("corporate_action", release_name)
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = duckdb.connect()
        try:
            connection.execute("SET threads = 1")
            stats = connection.execute(
                """
                SELECT count(*), min(epoch_ms(ex_date_ms + 28800000)::DATE),
                       max(epoch_ms(ex_date_ms + 28800000)::DATE)
                FROM read_parquet(?)
                """,
                [str(source_path)],
            ).fetchone()
            connection.execute(
                """
                COPY (
                    SELECT
                        thscode AS symbol,
                        epoch_ms(ex_date_ms + 28800000)::DATE AS ex_date,
                        dividend_per_share,
                        per_share_bonus,
                        allotment_ratio,
                        allotment_price,
                        currency,
                        year(epoch_ms(ex_date_ms + 28800000)::DATE) AS year
                    FROM read_parquet(?)
                ) TO ? (
                    FORMAT PARQUET,
                    COMPRESSION ZSTD,
                    PARTITION_BY (year),
                    PER_THREAD_OUTPUT false,
                    ROW_GROUP_SIZE 100000
                )
                """,
                [str(target), str(source_path)],
            )
        finally:
            connection.close()
        _compact_partitions(target)
        version = _version("corporate_action", release_name, target, source_path, stats)
        self._register(version)
        return version

    def append_payloads(
        self,
        dataset: str,
        payloads: list[dict[str, Any]],
        *,
        partition_date: date | None = None,
    ) -> Path:
        if not dataset.replace("_", "").isalnum():
            raise ValueError("dataset must contain only letters, numbers, and underscores")
        target_date = partition_date or date.today()
        target_dir = (
            self.root
            / dataset
            / f"year={target_date.year:04d}"
            / f"month={target_date.month:02d}"
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"part-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid4().hex[:8]}.parquet"
        connection = duckdb.connect()
        try:
            connection.execute(
                "CREATE TEMP TABLE payloads(captured_at TIMESTAMPTZ, payload JSON)"
            )
            captured_at = datetime.now(UTC)
            connection.executemany(
                "INSERT INTO payloads VALUES (?, ?)",
                [
                    (captured_at, dumps(payload, ensure_ascii=False, default=str))
                    for payload in payloads
                ],
            )
            connection.execute(
                "COPY payloads TO ? (FORMAT PARQUET, COMPRESSION ZSTD)",
                [str(target)],
            )
        finally:
            connection.close()
        return target

    def query_daily_bars(
        self,
        *,
        symbols: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        version = self.latest("daily_bar")
        filters = ["1 = 1"]
        params: list[Any] = [str(Path(version.path) / "**" / "*.parquet")]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            filters.append(f"symbol IN ({placeholders})")
            params.extend(symbol.upper() for symbol in symbols)
        if start_date:
            filters.append("trade_date >= ?")
            params.append(start_date)
        if end_date:
            filters.append("trade_date <= ?")
            params.append(end_date)
        params.append(limit)
        connection = duckdb.connect()
        try:
            cursor = connection.execute(
                f"""
                SELECT symbol, trade_date, open, high, low, close, volume, amount
                FROM read_parquet(?, hive_partitioning=true)
                WHERE {' AND '.join(filters)}
                ORDER BY trade_date, symbol
                LIMIT ?
                """,
                params,
            )
            columns = [column[0] for column in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            connection.close()

    def latest(self, dataset: str) -> LakeDatasetVersion:
        versions = [item for item in self._manifest() if item["dataset"] == dataset]
        if not versions:
            raise LookupError(f"dataset not found in manifest: {dataset}")
        return LakeDatasetVersion(**max(versions, key=lambda item: item["created_at"]))

    def prune_old_versions(self, dataset: str, *, keep: int = 1) -> list[str]:
        if keep < 1:
            raise ValueError("keep must be positive")
        manifest = self._manifest()
        versions = sorted(
            (item for item in manifest if item["dataset"] == dataset),
            key=lambda item: item["created_at"],
            reverse=True,
        )
        removed = versions[keep:]
        root = self.root.resolve()
        for item in removed:
            target = Path(item["path"]).resolve()
            if root not in target.parents:
                raise RuntimeError(f"refusing to remove path outside lake: {target}")
            if target.exists():
                rmtree(target)
        removed_keys = {(item["dataset"], item["release"]) for item in removed}
        self._write_manifest(
            [
                item
                for item in manifest
                if (item["dataset"], item["release"]) not in removed_keys
            ]
        )
        return [item["release"] for item in removed]

    def _existing_version(self, dataset: str, release: str) -> LakeDatasetVersion:
        for item in self._manifest():
            if item["dataset"] == dataset and item["release"] == release:
                return LakeDatasetVersion(**item)
        raise RuntimeError(f"lake directory exists without manifest entry: {dataset}/{release}")

    def _register(self, version: LakeDatasetVersion) -> None:
        manifest = self._manifest()
        manifest.append(asdict(version))
        self._write_manifest(manifest)

    def _write_manifest(self, manifest: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.manifest_path.with_suffix(".json.tmp")
        temporary.write_text(
            dumps({"versions": manifest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.manifest_path)

    def _manifest(self) -> list[dict[str, Any]]:
        if not self.manifest_path.exists():
            return []
        return loads(self.manifest_path.read_text(encoding="utf-8")).get("versions", [])


def _version(
    dataset: str,
    release: str,
    target: Path,
    source: Path,
    stats: tuple[Any, ...],
) -> LakeDatasetVersion:
    return LakeDatasetVersion(
        dataset=dataset,
        release=release,
        path=str(target.resolve()),
        source_path=str(source),
        source_sha256=_file_sha256(source),
        row_count=int(stats[0]),
        min_date=stats[1].isoformat() if stats[1] else None,
        max_date=stats[2].isoformat() if stats[2] else None,
        created_at=datetime.now(UTC).isoformat(),
    )


def _release_name(source: Path) -> str:
    return f"{source.stem}-{int(source.stat().st_mtime)}"


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _compact_partitions(root: Path) -> None:
    for partition in sorted({path.parent for path in root.rglob("*.parquet")}):
        if any(part.startswith(".") for part in partition.relative_to(root).parts):
            continue
        files = list(partition.glob("*.parquet"))
        if len(files) <= 1:
            continue
        if any(not path.is_file() or path.suffix != ".parquet" for path in partition.iterdir()):
            raise ValueError(f"partition contains non-Parquet content: {partition}")
        token = uuid4().hex
        staged = partition.parent / f".{partition.name}.compact-{token}"
        retired = partition.parent / f".{partition.name}.replacing-{token}"
        staged.mkdir()
        temporary = staged / "data.parquet"
        target_sql = temporary.as_posix().replace("'", "''")
        try:
            connection = duckdb.connect()
            try:
                connection.execute(
                    "COPY (SELECT * FROM read_parquet(?, hive_partitioning=false)) "
                    f"TO '{target_sql}' "
                    "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
                    [str(partition / "*.parquet")],
                )
                source_count = connection.execute(
                    "SELECT count(*) FROM read_parquet(?, hive_partitioning=false)",
                    [str(partition / "*.parquet")],
                ).fetchone()[0]
                compacted_count = connection.execute(
                    "SELECT count(*) FROM read_parquet(?)", [str(temporary)]
                ).fetchone()[0]
                if source_count != compacted_count:
                    raise ValueError(f"partition compaction row count changed: {partition}")
            finally:
                connection.close()
        except Exception:
            try:
                rmtree(staged)
            except OSError:
                logger.warning(
                    "Could not remove failed partition staging: %s", staged, exc_info=True
                )
            raise
        try:
            partition.rename(retired)
        except Exception:
            try:
                rmtree(staged)
            except OSError:
                logger.warning("Could not remove unpublished partition: %s", staged, exc_info=True)
            raise
        try:
            staged.rename(partition)
        except Exception:
            retired.rename(partition)
            try:
                rmtree(staged)
            except OSError:
                logger.warning("Could not remove unpublished partition: %s", staged, exc_info=True)
            raise
        try:
            rmtree(retired)
        except OSError:
            logger.warning("Could not remove retired partition: %s", retired, exc_info=True)
