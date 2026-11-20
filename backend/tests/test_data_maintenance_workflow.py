from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.schemas.operations import OperationsActionPreview, OperationsActionStartRequest
from app.services import data_maintenance_workflow as module
from app.services.data_maintenance_workflow import DataMaintenanceWorkflow
from app.services.factor_pipeline_workflow import FactorBatchResult
from app.services.operations_actions import OperationsActionConflictError, OperationsActionService


@pytest.fixture
def workflow(monkeypatch):
    state = {
        "cutoff": date(2026, 11, 19),
        "target": date(2026, 11, 19),
        "completed_through": date(2026, 11, 17),
        "newer_market_bars": False,
        "symbols": ["600001.SH", "600002.SH"],
        "calendar": [SimpleNamespace(exchange=name, is_open=True) for name in ("SH", "SZ", "BJ")],
        "sync_calls": [],
        "loop_calls": [],
    }
    monkeypatch.setattr(module, "completed_daily_cutoff", lambda: state["cutoff"])
    monkeypatch.setattr(module.settings, "hithink_finance_api_key", "isolated-test")
    monkeypatch.setattr(module.settings, "factor_reads_curated", True)
    monkeypatch.setattr(
        module,
        "PriceValueScreenRepository",
        lambda: SimpleNamespace(latest_open_date=lambda db, **kw: state["target"]),
    )
    monkeypatch.setattr(
        module,
        "TradingCalendarRepository",
        lambda: SimpleNamespace(get_many_on_date=lambda db, **kw: state["calendar"]),
    )
    monkeypatch.setattr(
        module,
        "SecurityRepository",
        lambda: SimpleNamespace(get_active_symbols=lambda db: state["symbols"]),
    )
    state["sync"] = SimpleNamespace(
        end_date=state["target"],
        advanced=True,
        rows_inserted=4,
        market_rows=4,
        batch_nos=("synthetic-batch",),
        coverage={"stock_coverage_rate": 1},
    )

    def sync(db, **kwargs):
        state["sync_calls"].append(kwargs)
        if "after_sync_symbols" in state:
            state["symbols"] = state["after_sync_symbols"]
        return state["sync"]

    monkeypatch.setattr(
        module,
        "AutoDataSyncService",
        lambda: SimpleNamespace(
            repository=SimpleNamespace(
                completed_through=lambda db: state["completed_through"],
                has_unadjusted_bars_after=lambda db, **kw: state["newer_market_bars"],
                require_calendar_coverage=lambda db, **kw: None,
            ),
            sync=sync,
        ),
    )
    factors = FactorBatchResult(state["target"], state["target"], "qfq")
    factors.services = {"price_volume": {"status": "success", "total_rows": 32, "run_ids": [8]}}
    state["loop"] = SimpleNamespace(
        job_id=71,
        factor_result=factors,
        failed=False,
        skipped=False,
        market_release_id="market-71",
        curated_release_id="curated-71",
        alternative_result=SimpleNamespace(status="success"),
    )

    def loop(**kwargs):
        state["loop_calls"].append(kwargs)
        return state["loop"]

    monkeypatch.setattr(module, "run_daily_loop", loop)
    state["job"] = SimpleNamespace(
        id=71,
        status="success",
        metadata_={"gate_status": "passed", "publish_eligible": True},
    )
    state["repository"] = SimpleNamespace(get=lambda db, task_id: state["job"])
    state["database"] = SimpleNamespace(commit=lambda: None)
    state["service"] = DataMaintenanceWorkflow()
    state["checks"] = state["service"].checks(None)
    return state


def execute(state):
    return state["service"].execute(
        state["database"],
        checks=state["checks"],
        session_factory=lambda: None,
        job_repository=state["repository"],
    )


def test_combined_preview_requires_complete_calendar_and_curated_inputs(workflow):
    service = workflow["service"]
    assert service.executable(workflow["checks"])
    workflow["calendar"] = workflow["calendar"][:1]
    assert not service.executable(service.checks(None))
    assert workflow["sync_calls"] == workflow["loop_calls"] == []


def test_combined_sync_freezes_target_across_midnight_and_reports_real_outputs(workflow):
    workflow["cutoff"] = date(2026, 11, 20)
    payload, status = execute(workflow)
    assert status == "success" and payload["problem_closed"] is True
    assert workflow["sync_calls"] == [{"cutoff": date(2026, 11, 19)}]
    assert workflow["loop_calls"][0]["target_date"] == date(2026, 11, 19)
    assert workflow["loop_calls"][0]["skip_sync"] is True
    assert payload["daily_loop_job_id"] == 71
    assert payload["recheck"]["factor_rows"] == 32
    assert payload["recheck"]["curated_release_id"] == "curated-71"
    assert payload["recheck"]["factor_services"]["price_volume"]["run_ids"] == [8]


@pytest.mark.parametrize(
    ("field", "value"), [("completed_through", date(2026, 11, 20)), ("newer_market_bars", True)]
)
def test_combined_sync_rejects_newer_market_data_before_writing(workflow, field, value):
    workflow["cutoff"] = date(2026, 11, 20)
    workflow[field] = value
    service = workflow["service"]
    new_checks = service.checks(None)
    assert new_checks["newer_market_data"]
    assert not service.executable(new_checks)
    with pytest.raises(ValueError, match="晚于原目标日"):
        execute(workflow)
    assert workflow["sync_calls"] == workflow["loop_calls"] == []


def test_changed_universe_blocks_before_any_business_write(workflow):
    workflow["symbols"].append("600003.SH")
    with pytest.raises(ValueError, match="范围"):
        execute(workflow)
    assert workflow["sync_calls"] == workflow["loop_calls"] == []


def test_reference_refresh_cannot_silently_expand_factor_scope(workflow):
    workflow["after_sync_symbols"] = [*workflow["symbols"], "600003.SH"]
    payload, status = execute(workflow)
    assert status == "partial" and payload["problem_closed"] is False
    assert payload["recheck"]["reason_codes"] == ["REFERENCE_UNIVERSE_CHANGED"]
    assert workflow["loop_calls"] == []


def test_daily_sync_behind_target_does_not_start_factor_calculation(workflow):
    workflow["sync"].end_date = date(2026, 11, 18)
    payload, status = execute(workflow)
    assert status == "partial" and payload["problem_closed"] is False
    assert payload["recheck"]["reason_codes"] == ["DAILY_SYNC_BEHIND_TARGET"]
    assert workflow["loop_calls"] == []


@pytest.mark.parametrize("failure", ["partial", "empty", "failed", "missing_job", "gate_blocked"])
def test_partial_unknown_or_failed_production_is_never_closed(workflow, failure):
    if failure == "partial":
        workflow["job"].status = "partial"
    elif failure == "empty":
        workflow["loop"].factor_result.services["price_volume"]["status"] = "empty"
    elif failure == "failed":
        workflow["loop"].failed = True
    elif failure == "missing_job":
        workflow["job"] = None
    else:
        workflow["job"].metadata_["gate_status"] = "blocked"
    payload, status = execute(workflow)
    assert status in {"partial", "failed"}
    assert payload["problem_closed"] is False


def test_operation_uses_frozen_preview_and_rejects_client_scope_parameters(workflow, monkeypatch):
    from app.services import operations_actions

    service = OperationsActionService(
        job_repository=SimpleNamespace(
            get=lambda db, action_id: SimpleNamespace(
                metadata_={"preview": {"checks": workflow["checks"]}}
            )
        )
    )
    called = []
    monkeypatch.setattr(
        operations_actions,
        "DataMaintenanceWorkflow",
        lambda: SimpleNamespace(
            execute=lambda db, **kwargs: (
                called.append(kwargs) or ({"problem_closed": True}, "success")
            )
        ),
    )
    request = OperationsActionStartRequest(
        action_type="daily_data_and_factors_sync", parameters={}, idempotency_key="test-combined"
    )
    assert service._normalize_parameters(request.action_type, {}) == {}
    with pytest.raises(ValueError, match="does not accept parameters"):
        service._normalize_parameters(request.action_type, {"target_date": "2099-01-01"})
    service._execute_action(None, action_id=1, action_type=request.action_type, parameters={})
    assert called[0]["checks"] == workflow["checks"]


@pytest.fixture
def action_start(monkeypatch, workflow):
    from app.services import operations_actions

    held = []
    created = []
    now = datetime.now(UTC)

    @contextmanager
    def lock(*args):
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    def create(db, **kwargs):
        assert held
        row = SimpleNamespace(
            id=1,
            metadata_=kwargs["metadata"],
            status="running",
            error_message=None,
            created_at=now,
            started_at=now,
            finished_at=None,
        )
        created.append(row)
        return row

    repository = SimpleNamespace(get_operator_request=lambda db, **kwargs: None, create=create)
    service = OperationsActionService(job_repository=repository)
    preview = OperationsActionPreview(
        action_type="daily_data_and_factors_sync",
        executable=True,
        summary="ready",
        parameters={},
        checks=deepcopy(workflow["checks"]),
    )

    def fresh_preview(db, **kwargs):
        assert held
        return preview

    monkeypatch.setattr(service, "preview", fresh_preview)
    monkeypatch.setattr(operations_actions, "duckdb_write_lock", lock)
    monkeypatch.setattr(operations_actions, "merge_job_lease_metadata", lambda db, m, **kw: m)
    return SimpleNamespace(
        service=service,
        preview=preview,
        created=created,
        database=SimpleNamespace(commit=lambda: None, refresh=lambda row: None),
        request=OperationsActionStartRequest(
            action_type="daily_data_and_factors_sync", parameters={}, idempotency_key="start-race"
        ),
    )


@pytest.mark.parametrize("changed_field", ["target_trade_date", "universe_sha256"])
def test_start_rejects_scope_changes_inside_writer_lock(action_start, changed_field):
    expected = action_start.preview.model_dump(mode="json")
    action_start.preview.checks[changed_field] = "changed-after-confirmation"
    with pytest.raises(OperationsActionConflictError, match="已变化"):
        action_start.service.start(
            action_start.database, request=action_start.request, expected_preview=expected
        )
    assert action_start.created == []


def test_start_preserves_confirmed_scope_and_ignores_only_display_fields(action_start):
    from app.services.agent_execution_contract import compact_preview

    expected = compact_preview(action_start.preview.model_dump(mode="json"))
    expected["_agent_grant"] = {"must_not_affect_preview_identity": True}
    expected["checked_at"] = "before-confirmation"
    result = action_start.service.start(
        action_start.database, request=action_start.request, expected_preview=expected
    )
    assert result.action_id == 1
    assert len(action_start.created) == 1
    assert action_start.created[0].metadata_["preview"]["checks"] == action_start.preview.checks
