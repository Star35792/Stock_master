"""Process leases and conservative recovery for ``metadata.job_log``.

The OS lock is the liveness authority.  Timestamps and PIDs are diagnostic
only: an old job is never failed merely because it looks stale.
"""

from __future__ import annotations

import atexit
import logging
import os
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.system import JobLogRepository
from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout, duckdb_write_lock

logger = logging.getLogger(__name__)

JOB_LEASE_KEY = JobLogRepository.LEASE_METADATA_KEY
JOB_LEASE_VERSION = JobLogRepository.LEASE_VERSION
JOB_LEASE_SLOT_COUNT = JobLogRepository.LEASE_SLOT_COUNT
ABANDONED_JOB_ERROR = "AbandonedJobError: owner process exited before terminal status"

LEASE_STATE_LEGACY = "legacy"
LEASE_STATE_INVALID = "invalid_lease"
LEASE_STATE_GUARD_MISSING = "guard_missing"
LEASE_STATE_LIVE = "live_owner"
LEASE_STATE_AUTO_RECOVERABLE = "auto_recoverable"


def _lease_root() -> Path:
    return Path(settings.metadata_db_path).resolve().parent / ".job_leases"


def _slot_path(root: Path, slot: int) -> Path:
    return root / f"process-slot-{slot:02d}.lock"


def _guard_path(root: Path, slot: int) -> Path:
    return Path(f"{_slot_path(root, slot)}.guard")


def _valid_lease(value: object) -> dict[str, Any] | None:
    return value if JobLogRepository.validate_lease(value) else None


def _require_clean_session(db: Session) -> None:
    """Recovery commits, so it must never run over a caller's pending business writes."""
    # Duck-typed test sessions carry no unit-of-work state, so only a real Session can be checked.
    if isinstance(db, Session) and (db.new or db.dirty or db.deleted):
        raise RuntimeError("job lease recovery requires a session without pending writes")


def merge_job_lease_metadata(
    db: Session,
    metadata: dict[str, Any] | None,
    *,
    repository: JobLogRepository,
) -> dict[str, Any]:
    """Return business metadata with immutable process-lease ownership."""
    payload = dict(metadata or {})
    if JOB_LEASE_KEY in payload:
        raise ValueError(f"{JOB_LEASE_KEY} is reserved for job infrastructure")
    payload[JOB_LEASE_KEY] = _PROCESS_LEASE.metadata(db, repository=repository)
    return payload


@dataclass(frozen=True)
class JobRecoveryResult:
    recovered: int = 0
    active: int = 0
    legacy_unknown: int = 0
    invalid_lease: int = 0


@dataclass(frozen=True)
class RunningJobState:
    job_id: int
    job_name: str
    started_at: datetime | None
    state: str
    slot: int | None


class _ProcessLease:
    """Hold one of a bounded set of process slots until interpreter exit."""

    def __init__(self) -> None:
        self._mutex = Lock()
        self._context: AbstractContextManager[Path] | None = None
        self._slot: int | None = None
        self._owner_token: str | None = None
        self._owner_started_at: str | None = None
        self._prepared = False
        self._registered = False

    def metadata(
        self,
        db: Session,
        *,
        repository: JobLogRepository,
    ) -> dict[str, Any]:
        with self._mutex:
            _require_clean_session(db)
            if self._context is None:
                self._acquire(db, repository=repository)
            elif not self._prepared and isinstance(repository, JobLogRepository):
                _recover_slot(
                    db,
                    slot=self._required_slot(),
                    repository=repository,
                    guard_preexisted=True,
                )
                self._prepared = True
            return {
                "version": JOB_LEASE_VERSION,
                "slot": self._required_slot(),
                "owner_token": self._owner_token,
                "owner_pid": os.getpid(),
                "owner_started_at": self._owner_started_at,
            }

    def _acquire(self, db: Session, *, repository: JobLogRepository) -> None:
        root = _lease_root()
        root.mkdir(parents=True, exist_ok=True)
        for slot in range(JOB_LEASE_SLOT_COUNT):
            guard_preexisted = _guard_path(root, slot).exists()
            if isinstance(repository, JobLogRepository) and not guard_preexisted:
                slots, _legacy, _invalid = _running_by_slot(
                    db,
                    repository=repository,
                )
                if slots.get(slot):
                    logger.error(
                        "Job lease slot %s has running rows but no guard; "
                        "preserving as unknown",
                        slot,
                    )
                    continue
            context = duckdb_write_lock(
                _slot_path(root, slot),
                timeout_seconds=0,
                poll_interval_seconds=0,
            )
            try:
                context.__enter__()
            except DuckDbWriteLockTimeout:
                continue
            except OSError as exc:
                logger.warning("Job lease slot %s could not be checked: %s", slot, exc)
                continue
            try:
                if isinstance(repository, JobLogRepository):
                    usable = _recover_slot(
                        db,
                        slot=slot,
                        repository=repository,
                        guard_preexisted=guard_preexisted,
                    )
                    if not usable:
                        continue
                    self._prepared = True
                self._context = context
                self._slot = slot
                self._owner_token = str(uuid4())
                self._owner_started_at = datetime.now(UTC).isoformat()
                if not self._registered:
                    atexit.register(self.close)
                    self._registered = True
                return
            finally:
                if self._context is not context:
                    context.__exit__(None, None, None)
        raise RuntimeError(
            f"no free job process lease slot (capacity={JOB_LEASE_SLOT_COUNT})"
        )

    def _required_slot(self) -> int:
        if self._slot is None:
            raise RuntimeError("job process lease is not acquired")
        return self._slot

    def close(self) -> None:
        with self._mutex:
            context = self._context
            self._context = None
            self._slot = None
            self._owner_token = None
            self._owner_started_at = None
            self._prepared = False
            if context is not None:
                context.__exit__(None, None, None)


_PROCESS_LEASE = _ProcessLease()


def _running_by_slot(
    db: Session,
    *,
    repository: JobLogRepository,
) -> tuple[dict[int, list[tuple[int, str]]], int, int]:
    slots: dict[int, list[tuple[int, str]]] = {}
    legacy = 0
    invalid = 0
    for job in repository.list_running(db):
        metadata = job.metadata_ or {}
        raw_lease = metadata.get(JOB_LEASE_KEY)
        if raw_lease is None:
            legacy += 1
            continue
        lease = _valid_lease(raw_lease)
        if lease is None:
            invalid += 1
            continue
        slots.setdefault(lease["slot"], []).append((job.id, lease["owner_token"]))
    return slots, legacy, invalid


def _slot_state(root: Path, slot: int) -> str:
    """Classify one slot without ever treating an unknown holder as dead."""
    if not _guard_path(root, slot).exists():
        return LEASE_STATE_GUARD_MISSING
    context = duckdb_write_lock(
        _slot_path(root, slot),
        timeout_seconds=0,
        poll_interval_seconds=0,
    )
    try:
        context.__enter__()
    except (DuckDbWriteLockTimeout, OSError):
        return LEASE_STATE_LIVE
    context.__exit__(None, None, None)
    return LEASE_STATE_AUTO_RECOVERABLE


def describe_running_jobs(
    db: Session,
    *,
    repository: JobLogRepository | None = None,
) -> list[RunningJobState]:
    """Report how each running row can be closed, without writing anything."""
    repo = repository or JobLogRepository()
    root = _lease_root()
    states: list[RunningJobState] = []
    for job in repo.list_running(db):
        raw_lease = (job.metadata_ or {}).get(JOB_LEASE_KEY)
        if raw_lease is None:
            states.append(
                RunningJobState(job.id, job.job_name, job.started_at, LEASE_STATE_LEGACY, None)
            )
            continue
        lease = _valid_lease(raw_lease)
        if lease is None:
            states.append(
                RunningJobState(job.id, job.job_name, job.started_at, LEASE_STATE_INVALID, None)
            )
            continue
        states.append(
            RunningJobState(
                job.id,
                job.job_name,
                job.started_at,
                _slot_state(root, lease["slot"]),
                lease["slot"],
            )
        )
    return states


def _recover_slot(
    db: Session,
    *,
    slot: int,
    repository: JobLogRepository,
    guard_preexisted: bool,
) -> bool:
    slots, _legacy, _invalid = _running_by_slot(db, repository=repository)
    rows = slots.get(slot, [])
    if rows and not guard_preexisted:
        logger.error(
            "Job lease slot %s has running rows but no guard; preserving as unknown",
            slot,
        )
        return False
    recovered_at = datetime.now(UTC).isoformat()
    for job_id, owner_token in rows:
        db.expire_all()
        job = repository.get(db, job_id)
        if job is None or job.status != "running":
            continue
        metadata = job.metadata_ or {}
        lease = _valid_lease(metadata.get(JOB_LEASE_KEY))
        if lease is None or lease["slot"] != slot or lease["owner_token"] != owner_token:
            continue
        recovery = {
            "recovery_reason": "owner_process_exited",
            "completion_status": "unknown",
            "recovered_at": recovered_at,
            "recovered_by_pid": os.getpid(),
            "recovery_version": JOB_LEASE_VERSION,
        }
        repository.fail_if_running(
            db,
            job_log=job,
            error_message=ABANDONED_JOB_ERROR,
            metadata=recovery,
        )
    db.commit()
    return True


def recover_abandoned_jobs(
    session_factory: Callable[[], Session],
    *,
    repository: JobLogRepository | None = None,
) -> JobRecoveryResult:
    """Recover leased running rows whose owner process lock is released."""
    repo = repository or JobLogRepository()
    with session_factory() as db:
        slots, legacy, invalid = _running_by_slot(db, repository=repo)
        recovered = 0
        active = 0
        root = _lease_root()
        for slot, initial_rows in slots.items():
            if not _guard_path(root, slot).exists():
                invalid += len(initial_rows)
                continue
            context = duckdb_write_lock(
                _slot_path(root, slot), timeout_seconds=0, poll_interval_seconds=0
            )
            try:
                context.__enter__()
            except DuckDbWriteLockTimeout:
                active += len(initial_rows)
                continue
            except OSError as exc:
                invalid += len(initial_rows)
                logger.warning("Job lease slot %s could not be checked: %s", slot, exc)
                continue
            try:
                initial_ids = {row[0] for row in initial_rows}
                before = sum(
                    1 for job in repo.list_running(db) if job.id in initial_ids
                )
                _recover_slot(
                    db,
                    slot=slot,
                    repository=repo,
                    guard_preexisted=True,
                )
                after = sum(
                    1 for job in repo.list_running(db) if job.id in initial_ids
                )
                recovered += before - after
            finally:
                context.__exit__(None, None, None)
        result = JobRecoveryResult(
            recovered=recovered,
            active=active,
            legacy_unknown=legacy,
            invalid_lease=invalid,
        )
        logger.info("Job recovery scan: %s", result)
        return result


def reconcile_legacy_running_jobs(
    db: Session,
    *,
    job_ids: list[int],
    reason: str,
    operator: str,
    apply: bool,
    repository: JobLogRepository | None = None,
    confirm_owner_exited: bool = False,
) -> list[Any]:
    """Plan or explicitly reconcile exact running rows automatic recovery will not close.

    Legacy rows (no lease) and leased rows whose slot is unverifiable (guard missing or
    invalid lease metadata) are operator-closable. Leased rows with a live or auto-recoverable
    slot must never be closed manually: the owner is alive or recovery will handle it.
    """
    repo = repository or JobLogRepository()
    _require_clean_session(db)
    normalized_ids = sorted(set(job_ids))
    if not normalized_ids:
        raise ValueError("at least one job id is required")
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("reason must not be blank")
    normalized_operator = operator.strip()
    if not normalized_operator:
        raise ValueError("operator must not be blank")
    root = _lease_root()
    plans: list[tuple[Any, str]] = []
    for job_id in normalized_ids:
        job = repo.get(db, job_id)
        if job is None:
            raise ValueError(f"job log not found: {job_id}")
        if job.status != "running":
            raise ValueError(f"job log {job_id} is not running")
        raw_lease = (job.metadata_ or {}).get(JOB_LEASE_KEY)
        if raw_lease is None:
            plans.append((job, "operator_confirmed_legacy"))
            continue
        if not confirm_owner_exited:
            raise ValueError(
                f"job log {job_id} has lease metadata; automatic recovery applies. "
                "Pass --confirm-owner-exited only when you have verified the slot is unverifiable."
            )
        lease = _valid_lease(raw_lease)
        state = _slot_state(root, lease["slot"]) if lease else LEASE_STATE_INVALID
        if state == LEASE_STATE_LIVE:
            raise ValueError(f"job log {job_id} slot is held by a live process; refusing")
        if state == LEASE_STATE_AUTO_RECOVERABLE:
            raise ValueError(f"job log {job_id} slot is free; automatic recovery applies")
        mode = (
            "operator_confirmed_guard_missing"
            if state == LEASE_STATE_GUARD_MISSING
            else "operator_confirmed_invalid_lease"
        )
        plans.append((job, mode))
    jobs = [job for job, _mode in plans]
    if not apply:
        return jobs
    reconciled_at = datetime.now(UTC).isoformat()
    for job, mode in plans:
        db.expire_all()
        fresh = repo.get(db, job.id)
        if fresh is None or fresh.status != "running":
            continue
        repo.fail_if_running(
            db,
            job_log=fresh,
            error_message=f"OperatorReconciledJob: {normalized_reason}",
            metadata={
                "recovery_mode": mode,
                "recovery_reason": normalized_reason,
                "completion_status": "unknown",
                "reconciled_at": reconciled_at,
                "reconciled_by": normalized_operator,
                "reconciled_by_pid": os.getpid(),
            },
        )
    db.commit()
    return jobs
