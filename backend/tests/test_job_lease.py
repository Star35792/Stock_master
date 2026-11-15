from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.session import initialize_database
from app.models.system import JobLog
from app.repositories.system import JobLogRepository
from app.services import job_lease
from app.services.job_lease import (
    ABANDONED_JOB_ERROR,
    JOB_LEASE_KEY,
    JOB_LEASE_SLOT_COUNT,
    JOB_LEASE_VERSION,
    LEASE_STATE_AUTO_RECOVERABLE,
    LEASE_STATE_GUARD_MISSING,
    LEASE_STATE_INVALID,
    LEASE_STATE_LEGACY,
    LEASE_STATE_LIVE,
    describe_running_jobs,
    reconcile_legacy_running_jobs,
    recover_abandoned_jobs,
)
from app.utils.duckdb_write_lock import duckdb_write_lock


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "job-lease.duckdb"
    engine = create_engine(f"duckdb:///{db_path}")
    initialize_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(job_lease.settings, "metadata_db_path", str(db_path))
    yield factory, tmp_path
    engine.dispose()


def _lease(slot: int = 0) -> dict[str, object]:
    return {
        "version": JOB_LEASE_VERSION,
        "slot": slot,
        "owner_token": str(uuid4()),
        "owner_pid": 999999,
        "owner_started_at": datetime.now(UTC).isoformat(),
    }


def _create_running(factory, *, metadata: dict) -> int:
    with factory() as db:
        job = JobLogRepository().create(
            db,
            job_name="crash_probe",
            job_type="schedule",
            metadata=metadata,
        )
        db.commit()
        return job.id


def test_live_child_process_is_preserved_then_recovered_after_exit(database) -> None:
    factory, root = database
    lease = _lease()
    lock_path = root / ".job_leases" / "process-slot-00.lock"
    lock_path.parent.mkdir(parents=True)
    script = (
        "import sys; "
        "from app.utils.duckdb_write_lock import duckdb_write_lock; "
        "cm=duckdb_write_lock(sys.argv[1], timeout_seconds=0, poll_interval_seconds=0); "
        "cm.__enter__(); print('ready', flush=True); sys.stdin.read()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ.copy(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        job_id = _create_running(factory, metadata={JOB_LEASE_KEY: lease})

        active = recover_abandoned_jobs(factory)
        assert active.active == 1
        with factory() as db:
            assert db.get(JobLog, job_id).status == "running"

        child.kill()
        child.wait(timeout=10)
        recovered = recover_abandoned_jobs(factory)
        assert recovered.recovered == 1
        with factory() as db:
            job = db.get(JobLog, job_id)
            assert job.status == "failed"
            assert job.error_message == ABANDONED_JOB_ERROR
            assert job.metadata_["completion_status"] == "unknown"
            assert job.metadata_["recovery_reason"] == "owner_process_exited"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_slot_reuse_recovers_previous_owner_before_returning_metadata(database) -> None:
    factory, root = database
    lease = _lease()
    job_id = _create_running(factory, metadata={JOB_LEASE_KEY: lease})
    guard = root / ".job_leases" / "process-slot-00.lock.guard"
    guard.parent.mkdir(parents=True)
    guard.write_bytes(b"\0")
    manager = job_lease._ProcessLease()
    try:
        with factory() as db:
            current = manager.metadata(db, repository=JobLogRepository())
            assert current["slot"] == 0
            db.expire_all()
            recovered = db.get(JobLog, job_id)
            assert recovered.status == "failed"
            assert recovered.metadata_["completion_status"] == "unknown"
    finally:
        manager.close()


def test_legacy_invalid_and_missing_guard_rows_fail_closed(database) -> None:
    factory, root = database
    with factory() as db:
        db.add_all(
            [
                JobLog(job_name="legacy", job_type="schedule", status="running"),
                JobLog(
                    job_name="invalid",
                    job_type="schedule",
                    status="running",
                    metadata_={JOB_LEASE_KEY: {"version": "unknown"}},
                ),
                JobLog(
                    job_name="incomplete_lease",
                    job_type="schedule",
                    status="running",
                    metadata_={
                        JOB_LEASE_KEY: {
                            "version": JOB_LEASE_VERSION,
                            "slot": 2,
                            "owner_token": str(uuid4()),
                            "owner_pid": 42,
                        }
                    },
                ),
                JobLog(
                    job_name="missing_guard",
                    job_type="schedule",
                    status="running",
                    metadata_={JOB_LEASE_KEY: _lease(slot=1)},
                ),
            ]
        )
        db.commit()

    result = recover_abandoned_jobs(factory)
    assert result.legacy_unknown == 1
    assert result.invalid_lease == 3
    assert not (root / ".job_leases" / "process-slot-01.lock.guard").exists()
    with factory() as db:
        assert {row.status for row in db.query(JobLog).all()} == {"running"}


def test_legacy_reconciliation_is_dry_run_by_default_and_audited(database) -> None:
    factory, _root = database
    with factory() as db:
        job = JobLog(job_name="legacy", job_type="schedule", status="running")
        db.add(job)
        db.commit()
        job_id = job.id

        planned = reconcile_legacy_running_jobs(
            db,
            job_ids=[job_id],
            reason="operator verified the old process is gone",
            operator="test-operator",
            apply=False,
        )
        assert planned[0].status == "running"
        db.expire_all()
        assert db.get(JobLog, job_id).status == "running"

        reconcile_legacy_running_jobs(
            db,
            job_ids=[job_id],
            reason="operator verified the old process is gone",
            operator="test-operator",
            apply=True,
        )
        db.expire_all()
        recovered = db.get(JobLog, job_id)
        assert recovered.status == "failed"
        assert recovered.metadata_["recovery_mode"] == "operator_confirmed_legacy"
        assert recovered.metadata_["completion_status"] == "unknown"
        assert recovered.metadata_["reconciled_by"] == "test-operator"


def test_repository_rejects_unleased_running_rows(database) -> None:
    factory, _root = database
    with factory() as db, pytest.raises(ValueError, match="require _job_lease"):
        JobLogRepository().create(
            db,
            job_name="unleased",
            job_type="schedule",
        )


def test_conditional_recovery_does_not_overwrite_terminal_status(database) -> None:
    factory, _root = database
    repo = JobLogRepository()
    with factory() as db:
        job = repo.create(
            db,
            job_name="already_finished",
            job_type="schedule",
            metadata={JOB_LEASE_KEY: _lease()},
        )
        db.commit()
        repo.finish(db, job_log=job, status="success")
        db.commit()

        repo.fail_if_running(
            db,
            job_log=job,
            error_message=ABANDONED_JOB_ERROR,
            metadata={"completion_status": "unknown"},
        )
        db.commit()
        db.expire_all()
        current = db.get(JobLog, job.id)
        assert current.status == "success"
        assert "completion_status" not in (current.metadata_ or {})


def _hold_slot(root: Path, slot: int):
    context = duckdb_write_lock(
        job_lease._slot_path(root / ".job_leases", slot),
        timeout_seconds=0,
        poll_interval_seconds=0,
    )
    context.__enter__()
    return context


def test_lease_acquisition_refuses_a_dirty_caller_session(database) -> None:
    factory, _root = database
    manager = job_lease._ProcessLease()
    try:
        with factory() as db:
            db.add(JobLog(job_name="pending", job_type="schedule", status="running"))
            with pytest.raises(RuntimeError, match="without pending writes"):
                manager.metadata(db, repository=JobLogRepository())
        with factory() as db:
            assert db.query(JobLog).filter(JobLog.job_name == "pending").count() == 0
    finally:
        manager.close()


def test_exhausted_slot_capacity_rejects_new_running_jobs(database) -> None:
    factory, root = database
    lease_dir = root / ".job_leases"
    lease_dir.mkdir(parents=True)
    held = []
    try:
        for slot in range(JOB_LEASE_SLOT_COUNT):
            context = duckdb_write_lock(
                job_lease._slot_path(lease_dir, slot),
                timeout_seconds=0,
                poll_interval_seconds=0,
            )
            context.__enter__()
            held.append(context)
        with factory() as db:
            with pytest.raises(RuntimeError, match="no free job process lease slot"):
                job_lease._ProcessLease().metadata(db, repository=JobLogRepository())
            assert db.query(JobLog).count() == 0
    finally:
        for context in held:
            context.__exit__(None, None, None)


def test_operator_cannot_close_a_row_whose_slot_is_live(database) -> None:
    factory, root = database
    job_id = _create_running(factory, metadata={JOB_LEASE_KEY: _lease(slot=2)})
    held = _hold_slot(root, 2)
    try:
        with factory() as db:
            with pytest.raises(ValueError, match="live process"):
                reconcile_legacy_running_jobs(
                    db,
                    job_ids=[job_id],
                    reason="operator guessed",
                    operator="op",
                    apply=True,
                    confirm_owner_exited=True,
                )
    finally:
        held.__exit__(None, None, None)
    with factory() as db:
        assert db.get(JobLog, job_id).status == "running"


def test_operator_cannot_close_a_row_that_recovers_automatically(database) -> None:
    factory, root = database
    job_id = _create_running(factory, metadata={JOB_LEASE_KEY: _lease(slot=4)})
    guard = job_lease._guard_path(root / ".job_leases", 4)
    guard.parent.mkdir(parents=True)
    guard.write_bytes(b"\0")
    with factory() as db:
        with pytest.raises(ValueError, match="automatic recovery applies"):
            reconcile_legacy_running_jobs(
                db,
                job_ids=[job_id],
                reason="operator guessed",
                operator="op",
                apply=True,
                confirm_owner_exited=True,
            )
    with factory() as db:
        assert db.get(JobLog, job_id).status == "running"


def test_operator_closes_guard_missing_leased_row_only_with_attestation(database) -> None:
    factory, root = database
    job_id = _create_running(factory, metadata={JOB_LEASE_KEY: _lease(slot=3)})
    with factory() as db:
        with pytest.raises(ValueError, match="--confirm-owner-exited"):
            reconcile_legacy_running_jobs(
                db,
                job_ids=[job_id],
                reason="owner machine rebooted",
                operator="op",
                apply=True,
            )
        planned = reconcile_legacy_running_jobs(
            db,
            job_ids=[job_id],
            reason="owner machine rebooted",
            operator="op",
            apply=False,
            confirm_owner_exited=True,
        )
        assert [job.id for job in planned] == [job_id]
    with factory() as db:
        assert db.get(JobLog, job_id).status == "running"
    with factory() as db:
        reconcile_legacy_running_jobs(
            db,
            job_ids=[job_id],
            reason="owner machine rebooted",
            operator="op",
            apply=True,
            confirm_owner_exited=True,
        )
    with factory() as db:
        job = db.get(JobLog, job_id)
        assert job.status == "failed"
        assert job.metadata_["recovery_mode"] == "operator_confirmed_guard_missing"
        assert job.metadata_["completion_status"] == "unknown"
        assert job.metadata_[JOB_LEASE_KEY]["slot"] == 3
    assert not job_lease._guard_path(root / ".job_leases", 3).exists()


def test_describe_running_jobs_classifies_every_row(database) -> None:
    factory, root = database
    lease_dir = root / ".job_leases"
    lease_dir.mkdir(parents=True)
    with factory() as db:
        db.add_all(
            [
                JobLog(job_name="legacy", job_type="schedule", status="running"),
                JobLog(
                    job_name="invalid",
                    job_type="schedule",
                    status="running",
                    metadata_={JOB_LEASE_KEY: {"version": "bogus"}},
                ),
                JobLog(
                    job_name="guard_missing",
                    job_type="schedule",
                    status="running",
                    metadata_={JOB_LEASE_KEY: _lease(slot=1)},
                ),
                JobLog(
                    job_name="recoverable",
                    job_type="schedule",
                    status="running",
                    metadata_={JOB_LEASE_KEY: _lease(slot=5)},
                ),
            ]
        )
        db.commit()
    job_lease._guard_path(lease_dir, 5).write_bytes(b"\0")
    held = _hold_slot(root, 6)
    with factory() as db:
        db.add(
            JobLog(
                job_name="live",
                job_type="schedule",
                status="running",
                metadata_={JOB_LEASE_KEY: _lease(slot=6)},
            )
        )
        db.commit()
    try:
        with factory() as db:
            states = {item.job_name: item for item in describe_running_jobs(db)}
        assert states["legacy"].state == LEASE_STATE_LEGACY
        assert states["invalid"].state == LEASE_STATE_INVALID
        assert states["guard_missing"].state == LEASE_STATE_GUARD_MISSING
        assert states["recoverable"].state == LEASE_STATE_AUTO_RECOVERABLE
        assert states["live"].state == LEASE_STATE_LIVE
        assert states["live"].slot == 6
    finally:
        held.__exit__(None, None, None)
    assert not job_lease._guard_path(lease_dir, 1).exists()
    with factory() as db:
        assert {job.status for job in db.query(JobLog).all()} == {"running"}
