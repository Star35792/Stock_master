from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.system import JobLog


class JobLogRepository:
    def get(self, db: Session, job_id: int) -> JobLog | None:
        return db.get(JobLog, job_id)

    def create(
        self,
        db: Session,
        *,
        job_name: str,
        job_type: str,
        batch_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JobLog:
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
            existing = job_log.metadata_ or {}
            existing.update(metadata)
            job_log.metadata_ = existing
        db.add(job_log)
        return job_log
