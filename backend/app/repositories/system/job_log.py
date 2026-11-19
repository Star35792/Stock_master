from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.system import JobLog


class JobLogRepository:
    LEASE_METADATA_KEY = "_job_lease"
    LEASE_VERSION = "process_slot_v1"
    LEASE_SLOT_COUNT = 32

    def get(self, db: Session, job_id: int) -> JobLog | None:
        return db.get(JobLog, job_id)

    def get_operator_request(self, db: Session, *, idempotency_key: str) -> JobLog | None:
        return db.scalar(
            select(JobLog)
            .where(
                JobLog.job_name == "operator_action",
                JobLog.metadata_["idempotency_key"].as_string() == idempotency_key,
            )
            .order_by(JobLog.id)
            .limit(1)
        )

    def list_running(self, db: Session) -> list[JobLog]:
        return list(
            db.scalars(select(JobLog).where(JobLog.status == "running").order_by(JobLog.id))
        )

    def list_recent_by_name(
        self,
        db: Session,
        *,
        job_name: str,
        limit: int = 100,
    ) -> list[JobLog]:
        return list(
            db.scalars(
                select(JobLog)
                .where(JobLog.job_name == job_name)
                .order_by(JobLog.started_at.desc(), JobLog.id.desc())
                .limit(limit)
            )
        )

    def create(
        self,
        db: Session,
        *,
        job_name: str,
        job_type: str,
        batch_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobLog:
        lease = (metadata or {}).get(self.LEASE_METADATA_KEY)
        if not self.validate_lease(lease):
            raise ValueError(f"running job logs require {self.LEASE_METADATA_KEY} metadata")
        job = JobLog(
            job_name=job_name,
            job_type=job_type,
            batch_id=batch_id,
            status="running",
            metadata_=metadata,
        )
        db.add(job)
        db.flush()
        return job

    def finish(
        self,
        db: Session,
        *,
        job_log: JobLog,
        status: str,
        rows_read: int = 0,
        rows_inserted: int = 0,
        rows_updated: int = 0,
        rows_failed: int = 0,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobLog:
        job_log.status = status
        job_log.finished_at = datetime.now(UTC)
        job_log.rows_read = rows_read
        job_log.rows_inserted = rows_inserted
        job_log.rows_updated = rows_updated
        job_log.rows_failed = rows_failed
        if error_message is not None:
            job_log.error_message = error_message
        if metadata is not None:
            # Assign a fresh dict so SQLAlchemy detects JSON metadata changes.
            existing = dict(job_log.metadata_ or {})
            existing_lease = existing.get(self.LEASE_METADATA_KEY)
            supplied_lease = metadata.get(self.LEASE_METADATA_KEY)
            if supplied_lease is not None and supplied_lease != existing_lease:
                raise ValueError(f"{self.LEASE_METADATA_KEY} metadata is immutable")
            existing.update(metadata)
            if existing_lease is not None:
                existing[self.LEASE_METADATA_KEY] = existing_lease
            job_log.metadata_ = existing
        db.add(job_log)
        return job_log

    def fail_if_running(
        self,
        db: Session,
        *,
        job_log: JobLog,
        error_message: str,
        metadata: dict[str, Any],
    ) -> None:
        """Conditionally fail a row without overwriting a concurrent terminal state."""
        merged = dict(job_log.metadata_ or {})
        merged.update(metadata)
        db.execute(
            update(JobLog)
            .where(JobLog.id == job_log.id, JobLog.status == "running")
            .values(
                status="failed",
                finished_at=datetime.now(UTC),
                error_message=error_message,
                metadata_=merged,
            )
        )

    @classmethod
    def validate_lease(cls, value: object) -> bool:
        if not isinstance(value, dict) or value.get("version") != cls.LEASE_VERSION:
            return False
        slot = value.get("slot")
        pid = value.get("owner_pid")
        if not isinstance(slot, int) or not 0 <= slot < cls.LEASE_SLOT_COUNT:
            return False
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            UUID(str(value.get("owner_token")))
            datetime.fromisoformat(str(value.get("owner_started_at")))
        except (TypeError, ValueError):
            return False
        return True
