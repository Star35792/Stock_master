from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from json import dumps
from pathlib import Path
from typing import Any

from app.config.settings import settings


@dataclass(frozen=True)
class RawLandingObject:
    path: Path
    sha256: str
    row_count: int
    size_bytes: int


class RawLandingStore:
    """Persists immutable raw capture objects on the local filesystem."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.raw_landing_root)

    def write_jsonl(
        self,
        *,
        source_name: str,
        dataset: str,
        batch_no: str,
        rows: Iterable[dict[str, Any]],
        partition_date: date | None = None,
    ) -> RawLandingObject:
        target = (
            self._target_dir(source_name, dataset, partition_date)
            / f"{_safe_name(batch_no)}.jsonl"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".jsonl.part")
        digest = sha256()
        row_count = 0
        with temporary.open("wb") as output:
            for row in rows:
                line = dumps(row, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                output.write(line)
                output.write(b"\n")
                digest.update(line)
                digest.update(b"\n")
                row_count += 1
        temporary.replace(target)
        return RawLandingObject(
            path=target.resolve(),
            sha256=digest.hexdigest(),
            row_count=row_count,
            size_bytes=target.stat().st_size,
        )

    def copy_file(
        self,
        source: str | Path,
        *,
        source_name: str,
        dataset: str,
        batch_no: str,
        partition_date: date | None = None,
    ) -> RawLandingObject:
        source_path = Path(source)
        suffix = source_path.suffix or ".bin"
        target = (
            self._target_dir(source_name, dataset, partition_date)
            / f"{_safe_name(batch_no)}{suffix}"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{suffix}.part")
        digest = sha256()
        with source_path.open("rb") as input_file, temporary.open("wb") as output:
            while chunk := input_file.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        temporary.replace(target)
        return RawLandingObject(
            path=target.resolve(),
            sha256=digest.hexdigest(),
            row_count=0,
            size_bytes=target.stat().st_size,
        )

    def _target_dir(
        self,
        source_name: str,
        dataset: str,
        partition_date: date | None,
    ) -> Path:
        captured_date = partition_date or datetime.now(UTC).date()
        return (
            self.root
            / _safe_name(source_name)
            / _safe_name(dataset)
            / f"year={captured_date.year:04d}"
            / f"month={captured_date.month:02d}"
        )


def _safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"_", "-", "."} else "_" for char in value)
    return safe.strip("._") or "unknown"
