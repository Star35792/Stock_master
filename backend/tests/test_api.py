from fastapi.testclient import TestClient

from app.api.routes import health as health_module
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
    assert "/api/v1/strategy-research/factor-selection/backtests" in paths
    assert "/api/v1/operations/overview" in paths


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
