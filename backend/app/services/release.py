"""Release service — immutable, provenance-bound data-lake releases."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.repositories.lake import ReleaseRepository


def _release_id_hash(
    *,
    dataset: str,
    checksum: str,
    schema_version: str,
    min_date: date | None,
    max_date: date | None,
    row_count: int,
    parent_release_id: str | None,
    code_version: str,
    metadata: dict[str, Any] | None,
) -> str:
    """Bind the ID to every immutable fact, including lineage and code version."""
    identity = {
        "dataset": dataset,
        "checksum": checksum,
        "schema_version": schema_version,
        "min_date": min_date.isoformat() if min_date else None,
        "max_date": max_date.isoformat() if max_date else None,
        "row_count": row_count,
        "parent_release_id": parent_release_id,
        "code_version": code_version,
        "metadata": metadata,
    }
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        """Reuse an identical release; changed lineage receives a distinct ID."""

        def parse_date(value: Any) -> date | None:
            if value is None or isinstance(value, date):
                return value
            return date.fromisoformat(str(value))

        min_date = parse_date(curate_result.min_date)
        max_date = parse_date(curate_result.max_date)
        release_id = _release_id_hash(
            dataset=dataset,
            checksum=checksum,
            schema_version=schema_version,
            min_date=min_date,
            max_date=max_date,
            row_count=curate_result.row_count,
            parent_release_id=parent_release_id,
            code_version=code_version,
            metadata=metadata,
        )
        if self.repository.get_by_release_id(db, release_id=release_id) is None:
            # Pre-upgrade releases used a short content-only ID. Reuse an exact
            # legacy fact rather than minting an equivalent second registry row.
            legacy_id = hashlib.sha256(f"{dataset}|{checksum}".encode()).hexdigest()[:16]
            legacy = self.repository.get_by_release_id(db, release_id=legacy_id)
            if legacy is not None and self.repository.matches_immutable_facts(
                legacy,
                dataset=dataset,
                schema_version=schema_version,
                min_date=min_date,
                max_date=max_date,
                row_count=curate_result.row_count,
                checksum=checksum,
                parent_release_id=parent_release_id,
                code_version=code_version,
                metadata=metadata,
            ):
                release_id = legacy_id
        release = self.repository.create(
            db,
            release_id=release_id,
            dataset=dataset,
            schema_version=schema_version,
            min_date=min_date,
            max_date=max_date,
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
    def release_id_hash(
        *,
        dataset: str,
        checksum: str,
        schema_version: str,
        min_date: date | None,
        max_date: date | None,
        row_count: int,
        parent_release_id: str | None,
        code_version: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return _release_id_hash(
            dataset=dataset,
            checksum=checksum,
            schema_version=schema_version,
            min_date=min_date,
            max_date=max_date,
            row_count=row_count,
            parent_release_id=parent_release_id,
            code_version=code_version,
            metadata=metadata,
        )
