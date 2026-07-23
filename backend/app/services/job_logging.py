"""统一任务日志服务。

为数据同步、因子计算、质量校验等后台任务提供 running -> success/failed/partial
的可观测记录，写入 ``system.job_log``。
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from app.models.system import JobLog
from app.repositories.system import JobLogRepository

logger = logging.getLogger(__name__)


@contextmanager
def logged_job(
    db: Session,
    *,
    job_name: str,
    job_type: str,
    batch_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    repository: JobLogRepository | None = None,
) -> Generator[JobLog]:
    """在 ``system.job_log`` 中记录一次任务的生命周期。

    进入时创建 ``running`` 记录并提交，确保即使后续业务事务回滚也能保留失败审计。
    正常退出时，若调用方未修改 ``job.status`` 则标记 ``success``；调用方可通过
    设置 ``job.status = "partial"``、``job.rows_*`` 和 ``job.metadata_`` 来反映
    部分成功。抛出异常时标记 ``failed`` 并重新抛出。
    """
    repo = repository or JobLogRepository()
    job = repo.create(
        db,
        job_name=job_name,
        job_type=job_type,
        batch_id=batch_id,
        metadata=metadata,
    )
    db.commit()
    logger.info("Job started: %s (%s) id=%s", job_name, job_type, job.id)
    try:
        yield job
        status = job.status if job.status != "running" else "success"
        repo.finish(
            db,
            job_log=job,
            status=status,
            rows_read=job.rows_read,
            rows_inserted=job.rows_inserted,
            rows_updated=job.rows_updated,
            rows_failed=job.rows_failed,
            metadata=job.metadata_,
        )
        db.commit()
        logger.info("Job %s: %s id=%s", status, job_name, job.id)
    except Exception as exc:  # noqa: BLE001 - log and re-raise
        error_message = f"{type(exc).__name__}: {exc}"
        repo.finish(
            db,
            job_log=job,
            status="failed",
            error_message=error_message,
            rows_read=job.rows_read,
            rows_inserted=job.rows_inserted,
            rows_updated=job.rows_updated,
            rows_failed=job.rows_failed,
            metadata=job.metadata_,
        )
        db.commit()
        logger.exception("Job failed: %s id=%s", job_name, job.id)
        raise
