from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import backtests as backtests_module
from app.api.routes import health as health_module
from app.api.routes import operations as operations_module
from app.api.routes import strategy_research as strategy_research_module
from app.main import app

client = TestClient(app)


def test_health(monkeypatch) -> None:
    monkeypatch.setattr(
        health_module,
        "check_database",
        lambda: health_module.HealthComponent(status="ok"),
    )
    monkeypatch.setattr(
        health_module,
        "check_redis",
        lambda: health_module.HealthComponent(status="ok"),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": {"status": "ok", "message": None},
        "redis": {"status": "ok", "message": None},
    }


def test_health_returns_503_when_dependency_is_down(monkeypatch) -> None:
    monkeypatch.setattr(
        health_module,
        "check_database",
        lambda: health_module.HealthComponent(status="ok"),
    )
    monkeypatch.setattr(
        health_module,
        "check_redis",
        lambda: health_module.HealthComponent(status="error", message="connection refused"),
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["redis"] == {"status": "error", "message": "connection refused"}


def test_openapi_contains_v1_routes() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/securities" in paths
    assert "/api/v1/daily-bars/{symbol}" in paths
    assert "/api/v1/daily-bars/missing" in paths
    assert "/api/v1/data-quality/issues" in paths
    assert "/api/v1/data-quality/issues/{issue_id}/status" in paths
    assert "/api/v1/daily-bar-source-compares" in paths
    assert "/api/v1/manual-corrections" in paths
    assert "/api/v1/corporate-actions" in paths
    assert "/api/v1/adjustment-factors" in paths
    assert "/api/v1/trading-calendar" in paths
    assert "/api/v1/securities/{symbol}/lifecycle" in paths
    assert "/api/v1/factors/definitions" in paths
    assert "/api/v1/factors/runs" in paths
    assert "/api/v1/factors/values" in paths
    assert "/api/v1/factors/profiles" in paths
    assert "/api/v1/backtests/runs" in paths
    assert "/api/v1/backtests/runs/{run_id}" in paths
    assert "/api/v1/backtests/runs/{run_id}/trades" in paths
    assert "/api/v1/backtests/runs/{run_id}/equity" in paths
    assert "/api/v1/backtests/runs/{run_id}/positions" in paths
    assert "/api/v1/backtests/runs/{run_id}/performance" in paths
    assert "/api/v1/backtests/runs/{run_id}/benchmark" in paths
    assert "/api/v1/backtests/runs/compare" in paths
    assert "/api/v1/strategy-research/factor-selection/backtests" in paths
    assert "/api/v1/strategy-research/factor-selection/backtests/batch" in paths
    assert "/api/v1/strategy-research/rolling-windows" in paths
    assert "/api/v1/backtests/runs/drift" in paths
    assert "/api/v1/operations/overview" in paths
    assert "/api/v1/operations/alerts" in paths
    assert "/api/v1/operations/failures" in paths
    assert "/api/v1/operations/failures/{failure_type}/{failure_id}" in paths
    assert "/api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan" in paths


def test_operations_failures_forwards_limit(monkeypatch) -> None:
    captured: dict = {}

    def fake_get_failures(db, **kwargs):
        captured.update(kwargs)
        return {
            "generated_at": "2026-07-20T00:00:00Z",
            "data_quality_issues": {
                "total": 0,
                "limit": kwargs["limit"],
                "has_more": False,
                "items": [],
            },
            "jobs": {"total": 0, "limit": kwargs["limit"], "has_more": False, "items": []},
            "factor_runs": {
                "total": 0,
                "limit": kwargs["limit"],
                "has_more": False,
                "items": [],
            },
            "backtest_runs": {
                "total": 0,
                "limit": kwargs["limit"],
                "has_more": False,
                "items": [],
            },
            "paper_orders": {
                "total": 0,
                "limit": kwargs["limit"],
                "has_more": False,
                "items": [],
            },
        }

    monkeypatch.setattr(operations_module.service, "get_failures", fake_get_failures)
    response = client.get("/api/v1/operations/failures", params={"limit": 7})

    assert response.status_code == 200
    assert captured["limit"] == 7


def test_operations_alerts_returns_candidates(monkeypatch) -> None:
    captured: dict = {}

    def fake_get_alerts(db):
        captured["called"] = True
        return {
            "generated_at": "2026-07-20T00:00:00Z",
            "items": [
                {
                    "key": "failed_backtests",
                    "severity": "critical",
                    "count": 1,
                    "title": "Failed backtests",
                    "detail": "Backtest runs ended in failed status",
                }
            ],
        }

    monkeypatch.setattr(operations_module.service, "get_alerts", fake_get_alerts)
    response = client.get("/api/v1/operations/alerts")

    assert response.status_code == 200
    assert captured["called"] is True


def test_operations_failure_detail_forwards_type_and_id(monkeypatch) -> None:
    captured: dict = {}

    def fake_get_failure_detail(db, **kwargs):
        captured.update(kwargs)
        return {
            "failure_type": kwargs["failure_type"],
            "paper_order": None,
            "data_quality_issue": None,
            "job": None,
            "factor_run": None,
            "backtest_run": None,
        }

    monkeypatch.setattr(
        operations_module.service,
        "get_failure_detail",
        fake_get_failure_detail,
    )
    response = client.get("/api/v1/operations/failures/paper_order/7")

    assert response.status_code == 200
    assert captured == {"failure_type": "paper_order", "failure_id": 7}


def test_operations_failure_detail_maps_missing_to_404(monkeypatch) -> None:
    monkeypatch.setattr(
        operations_module.service,
        "get_failure_detail",
        lambda db, **kwargs: None,
    )
    response = client.get("/api/v1/operations/failures/paper_order/7")

    assert response.status_code == 404
    assert response.json()["detail"] == "operations failure not found"


def test_operations_failure_rerun_plan_forwards_type_and_id(monkeypatch) -> None:
    captured: dict = {}

    def fake_get_rerun_plan(db, **kwargs):
        captured.update(kwargs)
        return {
            "failure_type": kwargs["failure_type"],
            "failure_id": kwargs["failure_id"],
            "supported": True,
            "action_type": "daily_bar_gap_backfill",
            "reason": "missing daily bar can be backfilled for the affected symbol/date",
            "parameters": {"symbols": ["AAA"]},
        }

    monkeypatch.setattr(operations_module.service, "get_rerun_plan", fake_get_rerun_plan)
    response = client.get("/api/v1/operations/failures/data_quality_issue/2/rerun-plan")

    assert response.status_code == 200
    assert captured == {"failure_type": "data_quality_issue", "failure_id": 2}


def test_operations_failure_rerun_plan_maps_missing_to_404(monkeypatch) -> None:
    monkeypatch.setattr(
        operations_module.service,
        "get_rerun_plan",
        lambda db, **kwargs: None,
    )
    response = client.get("/api/v1/operations/failures/paper_order/7/rerun-plan")

    assert response.status_code == 404
    assert response.json()["detail"] == "operations failure not found"


def test_rolling_window_generation(monkeypatch) -> None:
    captured: dict = {}

    def fake_generate(db, **kwargs):
        captured.update(kwargs)
        return [
            {
                "index": 0,
                "training_start_date": date(2026, 1, 2),
                "training_end_date": date(2026, 1, 8),
                "test_start_date": date(2026, 1, 9),
                "test_end_date": date(2026, 1, 13),
            }
        ]

    monkeypatch.setattr(
        strategy_research_module.service,
        "generate_rolling_windows",
        fake_generate,
    )
    response = client.post(
        "/api/v1/strategy-research/rolling-windows",
        json={
            "exchange": "SH",
            "start_date": "2026-01-01",
            "end_date": "2026-01-20",
            "training_days": 5,
            "test_days": 3,
            "step_days": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()[0]["training_start_date"] == "2026-01-02"
    assert captured["exchange"] == "SH"


def test_backtest_drift_forwards_baseline_contract(monkeypatch) -> None:
    captured: dict = {}

    def fake_detect(db, **kwargs):
        captured.update(kwargs)
        return {
            "baseline_run_id": kwargs["baseline_run_id"],
            "candidate_run_ids": kwargs["candidate_run_ids"],
            "return_tolerance": kwargs["return_tolerance"],
            "drawdown_tolerance": kwargs["drawdown_tolerance"],
            "return_spread": Decimal("0.01"),
            "drawdown_spread": Decimal("0.02"),
            "drift_detected": True,
        }

    monkeypatch.setattr(backtests_module.service, "detect_drift", fake_detect)
    response = client.post(
        "/api/v1/backtests/runs/drift",
        json={
            "baseline_run_id": 1,
            "candidate_run_ids": [2],
            "return_tolerance": "0.005",
            "drawdown_tolerance": "0.01",
        },
    )

    assert response.status_code == 200
    assert response.json()["drift_detected"] is True
    assert captured["baseline_run_id"] == 1
    assert captured["candidate_run_ids"] == [2]


def test_backtest_drift_maps_incomparable_runs_to_422(monkeypatch) -> None:
    def fake_detect(db, **kwargs):
        raise ValueError("backtest run is not comparable with baseline 1: 2")

    monkeypatch.setattr(backtests_module.service, "detect_drift", fake_detect)
    response = client.post(
        "/api/v1/backtests/runs/drift",
        json={"baseline_run_id": 1, "candidate_run_ids": [2]},
    )

    assert response.status_code == 422
    assert "not comparable" in response.json()["detail"]


def test_daily_bar_rejects_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/daily-bars/600519.SH",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_missing_daily_bar_rejects_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/daily-bars/missing",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_source_compares_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/daily-bar-source-compares",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_manual_corrections_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/manual-corrections",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_corporate_actions_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/corporate-actions",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_adjustment_factors_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/adjustment-factors",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_trading_calendar_rejects_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/trading-calendar",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_data_quality_issue_status_rejects_invalid_status() -> None:
    response = client.patch(
        "/api/v1/data-quality/issues/1/status",
        json={"status": "done"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "status must be one of: open, ignored, fixed, reopened, false_positive"
    )


def test_factor_runs_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/factors/runs",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_factor_values_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/factors/values",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_factor_profiles_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/factors/profiles",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_backtest_runs_reject_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/backtests/runs",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_factor_selection_backtest_rejects_reversed_date_range() -> None:
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests",
        json={
            "name": "bad-range",
            "factor_specs": [{"factor_name": "momentum_20", "factor_version": "v1-qfq"}],
            "top_n": 1,
            "allowed_security_types": ["stock"],
            "start_date": "2026-07-20",
            "end_date": "2026-07-01",
            "adjust_type": "qfq",
            "initial_cash": "100000",
            "code_version": "abc123",
            "data_version": "market-qfq-20260720",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_factor_selection_backtest_forwards_research_contract(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            run_id=7,
            result=SimpleNamespace(
                initial_cash=Decimal("100000"),
                final_equity=Decimal("101000"),
                total_return=Decimal("0.01"),
                trades=[],
                equity_curve=[],
                positions=[],
            ),
        )

    monkeypatch.setattr(
        strategy_research_module.service,
        "run_factor_selection",
        fake_run,
    )
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests",
        json={
            "name": "contract-api",
            "strategy_name": "factor_selection_v4",
            "factor_specs": [
                {
                    "factor_name": "momentum_20",
                    "factor_version": "v1-qfq",
                    "weight": "0.7",
                }
            ],
            "top_n": 1,
            "signal_lag": 1,
            "rebalance_rule": "daily",
            "holding_period": 1,
            "benchmark": "000300.SH",
            "allowed_security_types": ["stock"],
            "start_date": "2026-07-01",
            "end_date": "2026-07-02",
            "adjust_type": "qfq",
            "initial_cash": "100000",
            "code_version": "abc123",
            "data_version": "market-qfq-20260720",
        },
    )

    assert response.status_code == 200
    assert response.json()["strategy_name"] == "factor_selection_v4"
    assert captured["strategy_name"] == "factor_selection_v4"
    assert captured["benchmark"] == "000300.SH"
    assert captured["start_date"] == date(2026, 7, 1)


def test_validation_backtest_requires_training_period_before_test_period() -> None:
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests",
        json={
            "name": "bad-validation-window",
            "factor_specs": [{"factor_name": "momentum_20", "factor_version": "v1"}],
            "top_n": 1,
            "start_date": "2026-07-01",
            "end_date": "2026-07-10",
            "initial_cash": "100000",
            "code_version": "abc123",
            "data_version": "market-qfq-20260720",
            "validation_mode": "out_of_sample",
            "training_start_date": "2026-06-01",
            "training_end_date": "2026-07-01",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "training period must end before the backtest period"
