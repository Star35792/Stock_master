from dataclasses import dataclass, field

import pytest

from app.services.job_logging import logged_job


@dataclass
class FakeJobLog:
    id: int
    status: str = "running"
    rows_read: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_failed: int = 0
    error_message: str | None = None
    metadata_: dict | None = field(default_factory=dict)


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.committed = 0
        self.rolled_back = False

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *args: object) -> None:
        self.closed = True

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back = True


class FakeJobLogRepository:
    def __init__(self) -> None:
        self._next_id = 1
        self.created: list[dict] = []
        self.finished: list[dict] = []

    def create(
        self,
        db: object,
        *,
        job_name: str,
        job_type: str,
        batch_id: int | None = None,
        metadata: dict | None = None,
    ) -> FakeJobLog:
        job = FakeJobLog(id=self._next_id, metadata_=metadata)
        self._next_id += 1
        self.created.append(
            {
                "job_name": job_name,
                "job_type": job_type,
                "batch_id": batch_id,
                "metadata": metadata,
            }
        )
        return job

    def finish(
        self, db: object, *, job_log: FakeJobLog, status: str, **kwargs: object
    ) -> FakeJobLog:
        job_log.status = status
        for key in ("rows_read", "rows_inserted", "rows_updated", "rows_failed"):
            if key in kwargs:
                setattr(job_log, key, kwargs[key])
        if "error_message" in kwargs:
            job_log.error_message = kwargs["error_message"]
        if kwargs.get("metadata"):
            job_log.metadata_ = kwargs["metadata"]
        self.finished.append(
            {
                "status": status,
                "rows_read": job_log.rows_read,
                "rows_inserted": job_log.rows_inserted,
                "rows_failed": job_log.rows_failed,
                "error_message": job_log.error_message,
            }
        )
        return job_log


def test_logged_job_marks_success_on_normal_exit() -> None:
    session = FakeSession()
    repo = FakeJobLogRepository()

    with logged_job(
        session,
        job_name="test_job",
        job_type="import",
        metadata={"key": "value"},
        repository=repo,
    ) as job:
        job.rows_read = 10
        job.rows_inserted = 5

    assert len(repo.created) == 1
    assert repo.created[0]["job_name"] == "test_job"
    assert repo.created[0]["job_type"] == "import"
    assert repo.created[0]["metadata"]["key"] == "value"
    assert repo.created[0]["metadata"]["_job_lease"]["version"] == "process_slot_v1"
    assert len(repo.finished) == 1
    assert repo.finished[0]["status"] == "success"
    assert repo.finished[0]["rows_read"] == 10
    assert repo.finished[0]["rows_inserted"] == 5
    assert job.status == "success"
    assert session.committed == 2  # create + finish


def test_logged_job_marks_failed_on_exception() -> None:
    session = FakeSession()
    repo = FakeJobLogRepository()

    with pytest.raises(RuntimeError, match="boom"):
        with logged_job(
            session,
            job_name="test_job",
            job_type="import",
            repository=repo,
        ) as job:
            job.rows_read = 3
            raise RuntimeError("boom")

    assert len(repo.finished) == 1
    assert repo.finished[0]["status"] == "failed"
    assert "RuntimeError" in repo.finished[0]["error_message"]
    assert "boom" in repo.finished[0]["error_message"]
    assert repo.finished[0]["rows_read"] == 3
    assert session.committed == 2


def test_logged_job_uses_partial_when_status_set() -> None:
    session = FakeSession()
    repo = FakeJobLogRepository()

    with logged_job(
        session,
        job_name="test_job",
        job_type="schedule",
        repository=repo,
    ) as job:
        job.rows_inserted = 7
        job.status = "partial"

    assert repo.finished[0]["status"] == "partial"
    assert repo.finished[0]["rows_inserted"] == 7


def test_logged_job_audits_base_exception_before_reraising() -> None:
    session = FakeSession()
    repo = FakeJobLogRepository()

    with pytest.raises(KeyboardInterrupt):
        with logged_job(
            session,
            job_name="test_job",
            job_type="schedule",
            repository=repo,
        ):
            raise KeyboardInterrupt

    assert repo.finished[0]["status"] == "failed"
    assert repo.finished[0]["error_message"] == "KeyboardInterrupt: "
