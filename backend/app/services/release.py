"""Release service — immutable release registry for the data lake.

Each release is content-addressed by ``sha256(f"{dataset}|{checksum}")[:16]``,
guaranteeing idempotency: identical data produces the same ``release_id``.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.lake import ReleaseRepository


def _release_id_hash(dataset: str, checksum: str) -> str:
    return hashlib.sha256(f"{dataset}|{checksum}".encode()).hexdigest()[:16]


def _build_parquet_manifest(path: Path) -> list[dict[str, Any]]:
    """Return a deterministic manifest with a checksum for every Parquet file."""
    manifest: list[dict[str, Any]] = []
    parquet_files = sorted(
        path.rglob("*.parquet"),
        key=lambda item: item.relative_to(path).as_posix(),
    )
    for parquet in parquet_files:
        file_hasher = hashlib.sha256()
        with parquet.open("rb") as file_handle:
            while chunk := file_handle.read(65536):
                file_hasher.update(chunk)
        manifest.append(
            {
                "path": parquet.relative_to(path).as_posix(),
                "sha256": file_hasher.hexdigest(),
                "size_bytes": parquet.stat().st_size,
            }
        )
    return manifest


def _manifest_checksum(manifest: list[dict[str, Any]]) -> str:
    """Hash file identities as well as contents to avoid layout collisions."""
    hasher = hashlib.sha256()
    for item in manifest:
        payload = f"{item['path']}\0{item['sha256']}\0{item['size_bytes']}\n"
        hasher.update(payload.encode("utf-8"))
    return hasher.hexdigest()


def _compute_parquet_checksum(path: Path) -> str:
    return _manifest_checksum(_build_parquet_manifest(path))


class ReleaseService:
    def __init__(self, repository: ReleaseRepository | None = None) -> None:
        self.repository = repository or ReleaseRepository()

    def build_release(
        self,
        db: Session,
        *,
        dataset: str,
        curate_result: Any,
        checksum: str,
        parent_release_id: str | None,
        code_version: str,
        quality_status: str = "pending",
        schema_version: str = "v1",
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """Create or update a release record for a curated dataset.

        Uses content-addressed ``release_id`` so that identical data produces
        the same record (idempotent). Existing records are updated in-place.
        """
        release_id = _release_id_hash(dataset, checksum)

        def parse_date(value: Any) -> date | None:
            if value is None or isinstance(value, date):
                return value
            return date.fromisoformat(str(value))

        release = self.repository.create(
            db,
            release_id=release_id,
            dataset=dataset,
            schema_version=schema_version,
            min_date=parse_date(curate_result.min_date),
            max_date=parse_date(curate_result.max_date),
            row_count=curate_result.row_count,
            checksum=checksum,
            parent_release_id=parent_release_id,
            code_version=code_version,
            quality_status=quality_status,
            metadata=metadata,
        )
        return release

    @staticmethod
    def compute_parquet_checksum(path: Path) -> str:
        return _compute_parquet_checksum(path)

    @staticmethod
    def build_parquet_manifest(path: Path) -> list[dict[str, Any]]:
        return _build_parquet_manifest(path)

    @staticmethod
    def manifest_checksum(manifest: list[dict[str, Any]]) -> str:
        return _manifest_checksum(manifest)

    @staticmethod
    def release_id_hash(dataset: str, checksum: str) -> str:
        return _release_id_hash(dataset, checksum)
