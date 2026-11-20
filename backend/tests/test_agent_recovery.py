import errno
import json
from contextlib import nullcontext
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.agent_session import create_agent_engine, initialize_agent_database
from app.models.portfolio import PortfolioPlanRecord
from app.models.strategy import BacktestRun, TrainingArtifact
from app.models.system import JobLog
from app.repositories.agent import AgentStateConflict
from app.repositories.agent_recovery import (
    AGENT_EVIDENCE_KEY,
    AgentBacktestRepository,
    AgentRecoveryRepository,
)
from app.schemas.agent_recovery import AgentActionIdentity, AgentReadCheckpoint
from app.schemas.operations import OperationsActionPreview, OperationsActionStartRequest
from app.services import operations_actions
from app.services.agent_maintenance_contract import MAINTENANCE_ACTIONS
from app.services.agent_recovery import (
    AgentEvidenceRecovery,
    ReadRecoveryPolicy,
    ReadRecoveryStopped,
    read_failure_kind,
)
from app.services.agent_state import AgentStateStore
from app.services.operations_actions import OperationsActionConflictError, OperationsActionService
from app.utils.agent_identity import parameters_hash
from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout


def identity(**changes):
    return AgentActionIdentity(
        **{
            "confirmation_id": "confirmation-identity-00001",
            "tool": "run_factor_backtest",
            "parameters_hash": parameters_hash(changes.get("tool", "run_factor_backtest"), {}),
            "code_version": "test-code",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "tool,parameters",
    [
        ("sync_data_and_factors", {}),
        ("repair_factor_values", {"original_run_id": 7}),
        ("refresh_alternative_data", {"sources": ["eastmoney", "hithink"]}),
    ],
)
@pytest.mark.parametrize("status", ["success", "partial"])
def test_new_maintenance_tools_recover_original_operator_identity(
    databases, tool, parameters, status
):
    db, artifacts = databases
    receipt = identity(tool=tool, parameters_hash=parameters_hash(tool, parameters))
    job = JobLog(
        job_name="operator_action",
        job_type="repair",
        status=status,
        metadata_={
            "idempotency_key": f"agent:{receipt.confirmation_id}",
            "action_type": MAINTENANCE_ACTIONS[tool],
            "parameters": parameters,
            AGENT_EVIDENCE_KEY: receipt.model_dump(),
        },
    )
    db.add(job)
    db.commit()
    report = AgentEvidenceRecovery().inspect(
        db,
        artifacts,
        identity=receipt,
        parameters=parameters,
        task_id=job.id,
    )
    assert report.status == ("verified" if status == "success" else "partial")
    assert report.task_id == job.id


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += seconds


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (TimeoutError(), "network"),
        (ConnectionResetError(), "network"),
        (URLError(TimeoutError()), "network"),
        (HTTPError("https://example.invalid", 429, "secret", None, None), "rate_limit"),
        (HTTPError("https://example.invalid", 503, "secret", None, None), "upstream"),
        (DuckDbWriteLockTimeout("secret local path"), "temporary_lock"),
        (OSError(errno.ENETUNREACH, "secret"), "network"),
        (ValueError("financial eligibility failed"), "permanent"),
        (PermissionError("secret"), "permanent"),
        (OSError(errno.ENOSPC, "disk full"), "permanent"),
        (HTTPError("https://example.invalid", 403, "secret", None, None), "permanent"),
        (URLError("unknown"), "permanent"),
    ],
)
def test_only_typed_transient_read_errors_retry(exc, kind):
    assert read_failure_kind(exc) == kind


def checkpoint(**changes):
    return AgentReadCheckpoint(operation_hash="a" * 64, deadline=1030, **changes)


def test_read_attempts_reserved_before_calls_and_bounded():
    clock, saved = Clock(), []
    read = Mock(side_effect=[TimeoutError("private"), ConnectionError("private"), {"run_id": 7}])
    policy = ReadRecoveryPolicy(clock=clock, wait=clock.wait)
    assert policy.run(checkpoint(), operation_hash="a" * 64, read=read, save=saved.append) == {
        "run_id": 7
    }
    assert [(row.status, row.attempts) for row in saved] == [
        ("calling", 1),
        ("retryable", 1),
        ("calling", 2),
        ("retryable", 2),
        ("calling", 3),
        ("success", 3),
    ]
    assert clock.now == 1003
    assert "private" not in json.dumps([row.model_dump() for row in saved])


@pytest.mark.parametrize("error", [ValueError("bad input"), PermissionError(), OSError()])
def test_permanent_failures_are_not_retried(error):
    saved, read = [], Mock(side_effect=error)
    with pytest.raises(ReadRecoveryStopped):
        ReadRecoveryPolicy(clock=Clock()).run(
            checkpoint(), operation_hash="a" * 64, read=read, save=saved.append
        )
    assert read.call_count == 1
    assert saved[-1].status == "failed"


def test_retry_count_and_backoff_survive_new_sqlite_store(tmp_path):
    engine = create_agent_engine(str(tmp_path / "agent.db"))
    initialize_agent_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        store = AgentStateStore(factory)
        state = {
            "session_id": "s" * 32,
            "revision": 0,
            "retry": checkpoint(attempts=2, status="retryable", next_retry_at=1002).model_dump(),
        }
        state["revision"] = store.save(state)
        restored = AgentStateStore(factory).load(state["session_id"])

        def save(row):
            restored["retry"] = row.model_dump()
            restored["revision"] = store.save(restored)

        read = Mock(side_effect=TimeoutError())
        clock = Clock()
        with pytest.raises(ReadRecoveryStopped):
            ReadRecoveryPolicy(clock=clock, wait=clock.wait).run(
                AgentReadCheckpoint.model_validate(restored["retry"]),
                operation_hash="a" * 64,
                read=read,
                save=save,
            )
        assert clock.now == 1002
        assert read.call_count == 1
        assert store.load(state["session_id"])["retry"]["attempts"] == 3
        assert store.load(state["session_id"])["retry"]["status"] == "exhausted"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "initial",
    [
        {"status": "calling", "attempts": 1},
        {"status": "unknown", "attempts": 1},
        {"status": "success", "attempts": 1},
        {"status": "retryable", "attempts": 3},
    ],
)
def test_restored_unknown_or_consumed_calls_never_replay(initial):
    read = Mock()
    with pytest.raises(ReadRecoveryStopped):
        ReadRecoveryPolicy(clock=Clock()).run(
            checkpoint(**initial), operation_hash="a" * 64, read=read, save=Mock()
        )
    read.assert_not_called()


def test_persistence_failure_stops_before_call_and_after_result():
    read = Mock(return_value={"task_id": 1})
    policy = ReadRecoveryPolicy(clock=Clock())
    with pytest.raises(AgentStateConflict):
        policy.run(
            checkpoint(),
            operation_hash="a" * 64,
            read=read,
            save=Mock(side_effect=AgentStateConflict()),
        )
    read.assert_not_called()
    with pytest.raises(AgentStateConflict):
        policy.run(
            checkpoint(),
            operation_hash="a" * 64,
            read=read,
            save=Mock(side_effect=[None, AgentStateConflict()]),
        )
    assert read.call_count == 1


def test_cancel_and_changed_inputs_never_dispatch_or_replay():
    read, save = Mock(), Mock()
    policy = ReadRecoveryPolicy(clock=Clock())
    with pytest.raises(ReadRecoveryStopped):
        policy.run(
            checkpoint(), operation_hash="a" * 64, read=read, save=save, cancelled=lambda: True
        )
    read.assert_not_called()
    with pytest.raises(ReadRecoveryStopped):
        policy.run(checkpoint(), operation_hash="b" * 64, read=read, save=save)
    read.assert_not_called()


@pytest.fixture
def databases():
    control, research = create_engine("duckdb:///:memory:"), create_engine("duckdb:///:memory:")
    for engine, schemas, tables in [
        (control, ["metadata", "portfolio"], [JobLog, PortfolioPlanRecord]),
        (research, ["strategy"], [TrainingArtifact, BacktestRun]),
    ]:
        with engine.begin() as con:
            for schema in schemas:
                con.exec_driver_sql(f"CREATE SCHEMA {schema}")
        for table in tables:
            table.__table__.create(engine)
    with (
        sessionmaker(bind=control, expire_on_commit=False)() as db,
        sessionmaker(bind=research, expire_on_commit=False)() as artifacts,
    ):
        yield db, artifacts
    control.dispose()
    research.dispose()


def run_options():
    return dict(
        name="test",
        strategy_name="factor_selection",
        parameters={},
        code_version="test-code",
        data_version="trusted-test",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 2),
        initial_cash=Decimal("1000"),
    )


def add_job(db, receipt, *, status="success", result=None, **extra):
    job = JobLog(
        job_name="agent_action",
        job_type="schedule",
        status=status,
        metadata_={
            "confirmation_id": receipt.confirmation_id,
            "parameters": {},
            "tool": receipt.tool,
            AGENT_EVIDENCE_KEY: receipt.model_dump(),
            "result": result or {},
            **extra,
        },
    )
    db.add(job)
    db.commit()
    return job


def test_exact_old_job_match_and_duplicate_confirmation_rejected(databases):
    db, artifacts = databases
    receipt = identity(tool="sync_daily_data")
    job = JobLog(
        job_name="operator_action",
        job_type="repair",
        status="success",
        metadata_={
            "idempotency_key": f"agent:{receipt.confirmation_id}",
            "action_type": "daily_data_sync",
            "parameters": {},
            AGENT_EVIDENCE_KEY: receipt.model_dump(),
        },
    )
    db.add(job)
    db.flush()
    for i in range(105):
        db.add(
            JobLog(
                job_name="agent_action",
                job_type="schedule",
                status="failed",
                metadata_={"confirmation_id": f"other-{i}"},
            )
        )
    db.commit()
    recovery = AgentEvidenceRecovery()
    report = recovery.inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "verified" and report.task_id == job.id
    add_job(db, receipt)
    assert recovery.inspect(db, artifacts, identity=receipt, parameters={}).status == "conflict"


def test_backtest_evidence_is_bound_before_any_commit_and_no_replay(databases):
    _, artifacts = databases
    receipt = identity()
    repository = AgentBacktestRepository(receipt)
    run = repository.create_run(artifacts, **run_options())
    assert run.parameters[AGENT_EVIDENCE_KEY] == receipt.model_dump()
    repository.fail_run(artifacts, run=run, error_message="data qualification failed")
    artifacts.commit()
    assert AgentRecoveryRepository().backtests(artifacts, receipt.confirmation_id)[0].status == (
        "failed"
    )
    with pytest.raises(ValueError, match="不能重复执行"):
        repository.create_run(artifacts, **run_options())


@pytest.mark.parametrize("job_status", ["running", "partial", "failed", "success"])
def test_cross_database_half_commit_keeps_original_artifact_and_blocks(databases, job_status):
    db, artifacts = databases
    receipt = identity()
    run = AgentBacktestRepository(receipt).create_run(artifacts, **run_options())
    run.status = "success"
    artifacts.commit()
    job = add_job(db, receipt, status=job_status, result={"run_id": run.id})
    report = AgentEvidenceRecovery().inspect(
        db, artifacts, identity=receipt, parameters={}, task_id=job.id
    )
    assert report.status == ("verified" if job_status == "success" else job_status)
    assert report.artifact_id == run.id and report.artifact_verified
    assert report.outputs == (
        {"run_id": run.id, "task_id": job.id} if job_status == "success" else {}
    )
    assert not report.evidence_qualified and not report.replayed
    assert db.get(JobLog, job.id).status == job_status


def test_orphaned_artifact_and_lease_unknown_never_advance(databases):
    db, artifacts = databases
    receipt = identity()
    run = AgentBacktestRepository(receipt).create_run(artifacts, **run_options())
    run.status = "success"
    artifacts.commit()
    recovery = AgentEvidenceRecovery()
    assert recovery.inspect(db, artifacts, identity=receipt, parameters={}).status == "unknown"
    job = add_job(db, receipt, result={"run_id": run.id}, completion_status="unknown")
    report = recovery.inspect(db, artifacts, identity=receipt, parameters={}, task_id=job.id)
    assert report.status == "unknown" and not report.outputs


@pytest.mark.parametrize(
    "change",
    [
        {"parameters_hash": "b" * 64},
        {"code_version": "old-code"},
        {"tool": "create_portfolio_from_backtest"},
    ],
)
def test_mismatched_task_identity_is_blocked(databases, change):
    db, artifacts = databases
    receipt = identity()
    add_job(db, identity(**change))
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "conflict" and not report.outputs


def test_task_success_without_matching_artifact_is_not_success(databases):
    db, artifacts = databases
    receipt = identity()
    add_job(db, receipt, result={"run_id": 999})
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "unknown" and not report.outputs


def test_result_id_conflict_and_duplicate_research_artifact_are_blocked(databases):
    db, artifacts = databases
    receipt = identity()
    run = AgentBacktestRepository(receipt).create_run(artifacts, **run_options())
    run.status = "success"
    artifacts.commit()
    add_job(db, receipt, result={"run_id": run.id + 1})
    recovery = AgentEvidenceRecovery()
    assert recovery.inspect(db, artifacts, identity=receipt, parameters={}).status == "conflict"
    # Simulate legacy/manual evidence corruption, not a permitted creation path.
    artifacts.add(BacktestRun(**{**run_options(), "parameters": run.parameters}))
    artifacts.commit()
    assert recovery.inspect(db, artifacts, identity=receipt, parameters={}).status == "conflict"


def test_legacy_task_does_not_acquire_evidence_during_recovery(databases):
    db, artifacts = databases
    receipt = identity()
    db.add(
        JobLog(
            job_name="agent_action",
            job_type="schedule",
            status="success",
            metadata_={
                "confirmation_id": receipt.confirmation_id,
                "tool": receipt.tool,
                "result": {"run_id": 7},
            },
        )
    )
    db.commit()
    assert (
        AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={}).status
        == "conflict"
    )


def test_recovery_never_has_a_write_or_dispatch_dependency():
    repository = SimpleNamespace(jobs=Mock(return_value=[]), backtests=Mock(return_value=[]))
    report = AgentEvidenceRecovery(repository).inspect(
        None, None, identity=identity(), parameters={}
    )
    assert report.status == "unknown"
    assert set(vars(repository)) == {"jobs", "backtests"}


def test_success_arriving_after_cancel_or_deadline_does_not_advance():
    clock, saved = Clock(), []

    def late_result():
        clock.now += 40
        return {"run_id": 9}

    with pytest.raises(ReadRecoveryStopped, match="时间上限"):
        ReadRecoveryPolicy(clock=clock).run(
            checkpoint(), operation_hash="a" * 64, read=late_result, save=saved.append
        )
    assert saved[-1].status == "exhausted"
    clock.now = 1000
    stopped = False

    def cancelling_read():
        nonlocal stopped
        stopped = True
        return {"run_id": 9}

    with pytest.raises(ReadRecoveryStopped, match="已取消"):
        ReadRecoveryPolicy(clock=clock).run(
            checkpoint(),
            operation_hash="a" * 64,
            read=cancelling_read,
            save=saved.append,
            cancelled=lambda: stopped,
        )
    assert saved[-1].status == "cancelled"


def test_real_research_commit_survives_main_transaction_rollback(databases):
    db, artifacts = databases
    receipt = identity()
    job = add_job(db, receipt, status="running")
    run = AgentBacktestRepository(receipt).create_run(artifacts, **run_options())
    run.status = "success"
    artifacts.commit()
    job.status = "success"
    job.metadata_ = {**job.metadata_, "result": {"run_id": run.id}}
    db.flush()
    db.rollback()  # Crash/failure between the two stores' commits.
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "running" and report.artifact_id == run.id
    assert report.artifact_verified and not report.outputs
    assert db.get(JobLog, job.id).status == "running"
    assert artifacts.get(BacktestRun, run.id).status == "success"


def test_old_backtest_cannot_be_relabelled_as_a_confirmed_run(databases):
    db, artifacts = databases
    legacy = BacktestRun(**{**run_options(), "status": "success"})
    artifacts.add(legacy)
    artifacts.commit()
    receipt = identity()
    add_job(db, receipt, result={"run_id": legacy.id})
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "unknown" and report.artifact_id is None
    assert AGENT_EVIDENCE_KEY not in artifacts.get(BacktestRun, legacy.id).parameters


def test_operator_receipt_is_in_first_task_commit_and_legacy_cannot_replay(monkeypatch):
    monkeypatch.setattr(operations_actions.settings, "app_code_version", "test-code")
    receipt = identity(tool="sync_daily_data")
    request = OperationsActionStartRequest(
        action_type="daily_data_sync",
        parameters={},
        idempotency_key=f"agent:{receipt.confirmation_id}",
    )
    repository, db = Mock(), Mock()
    repository.get_operator_request.return_value = None
    repository.create.return_value = SimpleNamespace(id=71)
    service = OperationsActionService(job_repository=repository)
    service.preview = Mock(
        return_value=OperationsActionPreview(
            action_type="daily_data_sync", executable=True, summary="test", parameters={}
        )
    )
    service.to_read = Mock(return_value={"task_id": 71})
    monkeypatch.setattr(operations_actions, "duckdb_write_lock", lambda *a: nullcontext())
    monkeypatch.setattr(operations_actions, "merge_job_lease_metadata", lambda db, m, **kw: m)

    def verify_first_commit():
        assert repository.create.call_args.kwargs["metadata"][AGENT_EVIDENCE_KEY] == (
            receipt.model_dump()
        )

    db.commit.side_effect = verify_first_commit
    service.start(db, request=request, agent_identity=receipt.model_dump())
    assert db.commit.call_count == 1
    metadata = dict(repository.create.call_args.kwargs["metadata"])
    repository.get_operator_request.return_value = SimpleNamespace(metadata_=metadata)
    service.start(db, request=request, agent_identity=receipt.model_dump())
    assert repository.create.call_count == 1
    metadata.pop(AGENT_EVIDENCE_KEY)
    with pytest.raises(OperationsActionConflictError):
        service.start(db, request=request, agent_identity=receipt.model_dump())
    assert repository.create.call_count == 1


def test_operator_receipt_cannot_be_bound_to_another_request():
    receipt = identity(tool="sync_daily_data")
    service = OperationsActionService()
    with pytest.raises(OperationsActionConflictError):
        service._start_locked(
            Mock(),
            request=OperationsActionStartRequest(
                action_type="daily_data_sync", parameters={}, idempotency_key="another-confirmation"
            ),
            agent_identity=receipt.model_dump(),
        )


def test_task_parameters_cannot_change_under_an_unchanged_receipt(databases):
    db, artifacts = databases
    receipt = identity(tool="sync_daily_data")
    db.add(
        JobLog(
            job_name="operator_action",
            job_type="repair",
            status="success",
            metadata_={
                "idempotency_key": f"agent:{receipt.confirmation_id}",
                "action_type": "daily_data_sync",
                "parameters": {"unexpected_scope": "all"},
                AGENT_EVIDENCE_KEY: receipt.model_dump(),
            },
        )
    )
    db.commit()
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.status == "conflict" and not report.outputs


@pytest.mark.parametrize("incorrect", ["receipt", "result_id", "code_version", None])
def test_portfolio_requires_matching_persisted_plan_and_original_task(databases, incorrect):
    db, artifacts = databases
    receipt = identity(tool="create_portfolio_from_backtest")
    plan_receipt = (
        identity(tool=receipt.tool, parameters_hash="b" * 64)
        if incorrect == ("receipt")
        else receipt
    )
    plan = PortfolioPlanRecord(
        name="research-only",
        as_of_date=date(2024, 1, 2),
        parameters={},
        total_equity=Decimal("1000"),
        cash_before=Decimal("1000"),
        target_cash=Decimal("200"),
        cash_after=Decimal("200"),
        code_version="different" if incorrect == "code_version" else receipt.code_version,
        idempotency_key=f"agent:{receipt.confirmation_id}",
        input_snapshot={AGENT_EVIDENCE_KEY: plan_receipt.model_dump()},
    )
    db.add(plan)
    db.commit()
    job = add_job(db, receipt, result={"plan_id": plan.id + (incorrect == "result_id")})
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters={})
    assert report.artifact_id == plan.id
    assert report.status == ("verified" if incorrect is None else "conflict")
    assert report.outputs == ({"task_id": job.id} if incorrect is None else {})


@pytest.mark.parametrize("corrupt", [False, True])
def test_backtest_receipt_must_describe_the_actual_persisted_inputs(databases, corrupt):
    db, artifacts = databases
    args = {
        "name": "test",
        "start_date": "2024-01-01",
        "end_date": "2024-01-02",
        "initial_cash": "1000",
        "top_n": 10,
        "factor_name": "momentum_20",
        "factor_version": "v1",
        "higher_is_better": True,
    }
    receipt = identity(parameters_hash=parameters_hash("run_factor_backtest", args))
    options = run_options()
    options["parameters"] = {
        "top_n": 15 if corrupt else 10,
        "factor_specs": [
            {
                "factor_name": "momentum_20",
                "factor_version": "v1",
                "higher_is_better": True,
                "weight": "1",
            }
        ],
    }
    run = AgentBacktestRepository(receipt).create_run(artifacts, **options)
    run.status = "success"
    artifacts.commit()
    add_job(db, receipt, result={"run_id": run.id}, parameters=args)
    report = AgentEvidenceRecovery().inspect(db, artifacts, identity=receipt, parameters=args)
    assert report.status == ("conflict" if corrupt else "verified")


def test_operator_rejects_inconsistent_hash_before_creating_task(monkeypatch):
    monkeypatch.setattr(operations_actions.settings, "app_code_version", "test-code")
    receipt = identity(tool="sync_daily_data", parameters_hash="b" * 64)
    repository = Mock()
    with pytest.raises(OperationsActionConflictError):
        OperationsActionService(job_repository=repository)._start_locked(
            Mock(),
            request=OperationsActionStartRequest(
                action_type="daily_data_sync",
                parameters={},
                idempotency_key=f"agent:{receipt.confirmation_id}",
            ),
            agent_identity=receipt.model_dump(),
        )
    repository.create.assert_not_called()
