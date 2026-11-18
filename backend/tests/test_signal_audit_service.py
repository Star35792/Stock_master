from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import backtests
from app.main import app
from app.services import signal_audit as module
from app.services.signal_audit import (
    AuditRun,
    SignalAuditBusy,
    SignalAuditNotFound,
    SignalAuditService,
)
from app.strategies.inputs import StrategyFactorFrame, StrategyFactorObservation
from app.strategies.templates import FactorScoreSpec, MultiFactorStrategy

D = Decimal
DAY = date(2026, 1, 5)


def fixture_service():
    spec = FactorScoreSpec("momentum", "v1")
    frame = StrategyFactorFrame.from_rows(as_of_date=DAY + timedelta(days=4), rows_by_factor={
        spec: [StrategyFactorObservation(symbol, DAY + timedelta(days=i), D(value), 7)
               for i in range(5) for symbol, value in [("A", 1), ("B", 2)]]
    })
    types = {"A": "stock", "B": "stock"}
    signals = MultiFactorStrategy(top_n=1, allowed_security_types={"stock"}).generate_history(
        frame=frame, security_types=types
    )
    params = {
        "factor_specs": [{"factor_name": "momentum", "factor_version": "v1", "weight": "1",
                          "higher_is_better": True}],
        "top_n": 1, "allowed_security_types": ["stock"], "coverage_ratio": None,
        "weighting_mode": "equal_weight_fully_invested",
        "minimum_score_policy": "always_fully_invested", "minimum_score": None,
        "invested_weight": "1", "rebalance_rule": "daily", "rebalance_interval": 1,
        "start_date": DAY.isoformat(), "end_date": frame.as_of_date.isoformat(),
        "strategy_input_snapshot": frame.snapshot(),
        "signals": [{"signal_date": s.signal_date.isoformat(), "symbol": s.symbol,
                     "target_weight": str(s.target_weight)} for s in signals],
    }
    run = AuditRun("success", "source-test", DAY, frame.as_of_date, params)
    research = SimpleNamespace(load_signal_audit_frame=Mock(return_value=frame))
    service = SignalAuditService(run_loader=lambda _: run, code_version=lambda: "source-test",
                                 research=research)
    service.securities.get_security_types = Mock(return_value=types)
    service.calendar.list_open_dates = Mock(return_value=list(frame.observation_dates))
    return service, run, frame


def test_verified_run_passes_only_declared_scope_without_mutating_contract():
    service, run, _ = fixture_service()
    original = deepcopy(run.parameters)
    report = service.build_report(None, run_id=1)
    assert report.status == "passed", report.reason
    assert report.input_verified and report.baseline_matches_run
    assert report.scope == "factor_frame_to_signal"
    assert report.historical_replay_verified is False
    assert run.parameters == original


@pytest.mark.parametrize("kind", ["code", "snapshot", "selection", "unsupported", "signals",
                                  "constraints", "oversized", "long_window"])
def test_unverifiable_runs_are_insufficient(kind):
    service, run, _ = fixture_service()
    if kind == "code":
        run = replace(run, code_version="old")
    elif kind == "snapshot":
        run.parameters["strategy_input_snapshot"]["sha256"] = "changed"
    elif kind == "selection":
        run.parameters["selection_policy"] = "training_ic_v1"
    elif kind == "unsupported":
        del run.parameters["factor_specs"]
    elif kind == "signals":
        run.parameters["signals"] = []
    elif kind == "constraints":
        run.parameters["portfolio_constraints"] = {"max_symbol_weight": "0.1"}
    elif kind == "oversized":
        run = replace(run, oversized=True)
    else:
        run = replace(run, end_date=DAY + timedelta(days=400))
    service.run_loader = lambda _: run
    assert service.build_report(None, run_id=1).status == "insufficient"
    if kind in {"code", "selection", "unsupported", "constraints", "oversized", "long_window"}:
        service.research.load_signal_audit_frame.assert_not_called()


def test_changed_security_type_cannot_masquerade_as_original_run():
    service, _, _ = fixture_service()
    service.securities.get_security_types.return_value = {"A": "stock", "B": "etf"}
    report = service.build_report(None, run_id=1)
    assert report.status == "insufficient"
    assert not report.baseline_matches_run


def test_changed_historical_rebalance_calendar_fails_baseline_check():
    service, run, frame = fixture_service()
    run.parameters["rebalance_rule"] = "every_n_trading_days"
    service.calendar.list_open_dates.return_value = list(frame.observation_dates)[1:]
    report = service.build_report(None, run_id=1)
    assert report.status == "insufficient"
    assert not report.baseline_matches_run


@pytest.mark.parametrize("kind", ["factor", "security", "calendar", "code"])
def test_within_request_drift_invalidates_report(kind):
    service, run, frame = fixture_service()
    if kind == "factor":
        service.research.load_signal_audit_frame.side_effect = [
            frame, StrategyFactorFrame.from_rows(as_of_date=frame.as_of_date, rows_by_factor={
                spec: [replace(row, value=row.value + 1) for row in rows]
                for spec, rows in frame.rows_by_factor.items()
            })
        ]
    elif kind == "security":
        service.securities.get_security_types.side_effect = [
            {"A": "stock", "B": "stock"}, {"A": "etf", "B": "stock"}
        ]
    elif kind == "calendar":
        run.parameters["rebalance_rule"] = "every_n_trading_days"
        service.calendar.list_open_dates.side_effect = [list(frame.observation_dates), []]
    else:
        service.code_version = Mock(side_effect=["source-test", "changed"])
    report = service.build_report(None, run_id=1)
    assert report.status == "insufficient", report.reason
    assert not report.input_verified


def test_global_single_flight_returns_busy_and_releases_after_failure():
    service, _, _ = fixture_service()
    module._AUDIT_LOCK.acquire()
    try:
        with pytest.raises(SignalAuditBusy):
            service.build_report(None, run_id=1)
    finally:
        module._AUDIT_LOCK.release()
    service.run_loader = Mock(side_effect=SignalAuditNotFound("missing"))
    with pytest.raises(SignalAuditNotFound):
        service.build_report(None, run_id=1)
    assert not module._AUDIT_LOCK.locked()


def test_short_research_session_and_lock_closed_before_computation(monkeypatch):
    from contextlib import contextmanager

    events = []
    service, run, _ = fixture_service()

    @contextmanager
    def lock(*args, **kwargs):
        events.append("lock")
        yield
        events.append("unlock")

    @contextmanager
    def session():
        events.append("session")
        yield None
        events.append("close")

    monkeypatch.setattr(module, "duckdb_write_lock", lock)
    monkeypatch.setattr(module, "ResearchSessionLocal", session)
    monkeypatch.setattr(module.BacktestRepository, "signal_audit_header",
                        lambda *a, **k: {"parameter_chars": 100})
    monkeypatch.setattr(module.BacktestRepository, "get_run", lambda *a, **k: run)
    service.run_loader = module.load_audit_run
    original_inputs = service._inputs

    def inputs(*args):
        assert events == ["lock", "session", "close", "unlock"]
        return original_inputs(*args)
    service._inputs = inputs
    assert service.build_report(None, run_id=1).status == "passed"


@pytest.mark.parametrize("failure,code", [(SignalAuditNotFound, 404), (SignalAuditBusy, 409)])
def test_endpoint_maps_errors_without_research_dependency(monkeypatch, failure, code):
    monkeypatch.setattr(backtests.signal_audit_service, "build_report",
                        Mock(side_effect=failure("reason")))
    response = TestClient(app).post("/api/v1/backtests/runs/1/signal-audit")
    assert response.status_code == code
    assert response.json()["detail"] == "reason"


def test_endpoint_returns_typed_report(monkeypatch):
    service, _, _ = fixture_service()
    report = service.build_report(None, run_id=1)
    monkeypatch.setattr(backtests.signal_audit_service, "build_report", lambda *a, **k: report)
    response = TestClient(app).post("/api/v1/backtests/runs/1/signal-audit")
    assert response.status_code == 200
    assert response.json()["status"] == "passed"
    assert isinstance(response.json()["checks"][0]["examples"], list)
