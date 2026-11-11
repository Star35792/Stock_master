"""Versioned evidence identities for persisted factor profiles.

The factor profile table predates first-class provenance columns.  Until that
schema is deliberately redesigned, ``evidence_eligibility_reason`` carries a
compact, machine-validated envelope.  Free-form legacy reasons remain readable
but fail closed for ``core`` qualification.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

EVIDENCE_IDENTITY_PREFIX = "evidence_identity_v1:"
EVIDENCE_IDENTITY_MAX_LENGTH = 500
_IDENTITY_KEYS = frozenset({"v", "r", "x", "d", "ws", "we", "s", "e", "n", "h"})


def _text_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def hash_trade_dates(trade_dates: Iterable[date]) -> str:
    """Hash an ordered, duplicate-free date identity."""
    normalized = sorted(set(trade_dates))
    payload = "\n".join(item.isoformat() for item in normalized)
    return sha256(payload.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class ProfileEvidenceIdentity:
    release_tag: str
    source_snapshot_sha256: str
    trade_date_sha256: str
    requested_start_date: date
    requested_end_date: date
    start_date: date
    end_date: date
    trade_date_count: int
    identity_sha256: str

    def matches(
        self,
        *,
        factor_name: str,
        factor_version: str,
        code_version: str | None,
        first_trade_date: date,
        last_trade_date: date,
        trade_date_count: int,
        trade_date_sha256: str,
    ) -> bool:
        return (
            code_version is not None
            and self.start_date == first_trade_date
            and self.end_date == last_trade_date
            and self.trade_date_count == trade_date_count
            and self.trade_date_sha256 == trade_date_sha256
            and self.identity_sha256
            == _identity_checksum(
                release_tag=self.release_tag,
                source_snapshot_sha256=self.source_snapshot_sha256,
                trade_date_sha256=self.trade_date_sha256,
                requested_start_date=self.requested_start_date,
                requested_end_date=self.requested_end_date,
                start_date=first_trade_date,
                end_date=last_trade_date,
                trade_date_count=trade_date_count,
                factor_name=factor_name,
                factor_version=factor_version,
                code_version=code_version,
            )
        )


def _source_snapshot_hash(factor_sha256: str, price_sha256: str) -> str:
    return _text_hash(f"factor={factor_sha256}\nprices_qfq={price_sha256}\n")


def _identity_checksum(
    *,
    release_tag: str,
    source_snapshot_sha256: str,
    trade_date_sha256: str,
    requested_start_date: date,
    requested_end_date: date,
    start_date: date,
    end_date: date,
    trade_date_count: int,
    factor_name: str,
    factor_version: str,
    code_version: str,
) -> str:
    payload = {
        "release_tag": release_tag,
        "source_snapshot_sha256": source_snapshot_sha256,
        "trade_date_sha256": trade_date_sha256,
        "requested_start_date": requested_start_date.isoformat(),
        "requested_end_date": requested_end_date.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "trade_date_count": trade_date_count,
        "factor_name": factor_name,
        "factor_version": factor_version,
        "code_version": code_version,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class ParquetContentSnapshot:
    sha256: str
    files: tuple[dict[str, object], ...]
    source_checksums: tuple[dict[str, object], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "sha256": self.sha256,
            "file_count": len(self.files),
            "source_checksums": self.source_checksums,
            "files": self.files,
        }


def build_parquet_content_snapshot(
    sources: Iterable[tuple[str, Path]],
) -> ParquetContentSnapshot:
    """Hash every actually addressable Parquet byte under labelled roots."""
    manifest: list[dict[str, object]] = []
    source_checksums: list[dict[str, object]] = []
    for label, root in sorted(sources, key=lambda item: item[0]):
        if not root.is_dir():
            raise FileNotFoundError(f"parquet snapshot root not found: {root}")
        source_manifest: list[dict[str, object]] = []
        for parquet in sorted(
            root.rglob("*.parquet"),
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            file_digest = sha256()
            with parquet.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    file_digest.update(chunk)
            item = {
                "source": label,
                "path": parquet.relative_to(root).as_posix(),
                "size_bytes": parquet.stat().st_size,
                "sha256": file_digest.hexdigest(),
            }
            manifest.append(item)
            source_manifest.append(item)
        source_digest = sha256()
        for item in source_manifest:
            source_digest.update(
                (
                    f"{item['path']}\0{item['sha256']}\0{item['size_bytes']}\n"
                ).encode()
            )
        source_checksums.append(
            {
                "source": label,
                "sha256": source_digest.hexdigest(),
                "file_count": len(source_manifest),
            }
        )
    if not manifest:
        raise ValueError("parquet content snapshot contains no files")
    digest = sha256()
    for item in source_checksums:
        digest.update(
            (
                f"{item['source']}\0{item['sha256']}\0{item['file_count']}\n"
            ).encode()
        )
    return ParquetContentSnapshot(
        sha256=digest.hexdigest(),
        files=tuple(manifest),
        source_checksums=tuple(source_checksums),
    )


def build_profile_evidence_identity(
    *,
    release_tag: str,
    factor_snapshot_sha256: str,
    price_snapshot_sha256: str,
    trade_date_sha256: str,
    requested_start_date: date,
    requested_end_date: date,
    start_date: date,
    end_date: date,
    trade_date_count: int,
    factor_name: str,
    factor_version: str,
    code_version: str,
) -> str:
    """Build the canonical envelope stored in the existing reason column."""
    if not release_tag or len(release_tag) > 64:
        raise ValueError("release_tag must contain 1 to 64 characters")
    if (
        trade_date_count <= 0
        or requested_start_date > requested_end_date
        or start_date > end_date
        or start_date < requested_start_date
        or end_date > requested_end_date
    ):
        raise ValueError("evidence identity requires a non-empty ordered date window")
    hashes = (factor_snapshot_sha256, price_snapshot_sha256, trade_date_sha256)
    if any(
        len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
        for value in hashes
    ):
        raise ValueError("evidence snapshot hashes must be full SHA-256 hex digests")
    source_snapshot_sha256 = _source_snapshot_hash(
        factor_snapshot_sha256, price_snapshot_sha256
    )
    identity_sha256 = _identity_checksum(
        release_tag=release_tag,
        source_snapshot_sha256=source_snapshot_sha256,
        trade_date_sha256=trade_date_sha256,
        requested_start_date=requested_start_date,
        requested_end_date=requested_end_date,
        start_date=start_date,
        end_date=end_date,
        trade_date_count=trade_date_count,
        factor_name=factor_name,
        factor_version=factor_version,
        code_version=code_version,
    )
    payload = {
        "v": 1,
        "r": release_tag,
        "x": source_snapshot_sha256,
        "d": trade_date_sha256,
        "ws": requested_start_date.isoformat(),
        "we": requested_end_date.isoformat(),
        "s": start_date.isoformat(),
        "e": end_date.isoformat(),
        "n": trade_date_count,
        "h": identity_sha256,
    }
    encoded = EVIDENCE_IDENTITY_PREFIX + json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(encoded) > EVIDENCE_IDENTITY_MAX_LENGTH:
        raise ValueError("evidence identity exceeds the existing 500-character column")
    return encoded


def parse_profile_evidence_identity(value: str) -> ProfileEvidenceIdentity | None:
    """Parse a canonical identity; legacy prose and malformed payloads fail closed."""
    if not value.startswith(EVIDENCE_IDENTITY_PREFIX):
        return None
    try:
        payload = json.loads(value.removeprefix(EVIDENCE_IDENTITY_PREFIX))
        if not isinstance(payload, dict) or frozenset(payload) != _IDENTITY_KEYS:
            return None
        hashes = tuple(str(payload[key]) for key in ("x", "d", "h"))
        if any(
            len(item) != 64
            or any(ch not in "0123456789abcdef" for ch in item)
            for item in hashes
        ):
            return None
        release_tag = str(payload["r"])
        count = int(payload["n"])
        requested_start = date.fromisoformat(str(payload["ws"]))
        requested_end = date.fromisoformat(str(payload["we"]))
        start = date.fromisoformat(str(payload["s"]))
        end = date.fromisoformat(str(payload["e"]))
        if payload["v"] != 1 or not release_tag or len(release_tag) > 64:
            return None
        if (
            count <= 0
            or requested_start > requested_end
            or start > end
            or start < requested_start
            or end > requested_end
        ):
            return None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return ProfileEvidenceIdentity(
        release_tag=release_tag,
        source_snapshot_sha256=hashes[0],
        trade_date_sha256=hashes[1],
        requested_start_date=requested_start,
        requested_end_date=requested_end,
        start_date=start,
        end_date=end,
        trade_date_count=count,
        identity_sha256=hashes[2],
    )
