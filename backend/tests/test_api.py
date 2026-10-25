from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.routes import (
    approvals as approvals_module,
    backtests as backtests_module,
    daily_bars as daily_bars_module,
    factors as factors_module,
    health as health_module,
    operations as operations_module,
    paper_accounts as paper_accounts_module,
    portfolio as portfolio_module,
    strategy_research as strategy_research_module,
)
from app.main import app
from app.services import (
    FactorBatchProfilerRunResult,
    FactorProfileResult,
    FactorProfileSummary,
)
from app.services.paper_approval import (
    PaperApprovalConflict,
    PaperApprovalNotFound,
    PaperApprovalValidationError,
)
from app.services.paper_equity import PaperEquitySnapshotNotFound
from app.services.portfolio import PortfolioIdempotencyConflict
from app.services.portfolio_readiness import PortfolioPlanNotFound

client = TestClient(app)


def test_health(monkeypatch) -> None:
    monkeypatch.setattr(
        health_module,
        "check_database",
        lambda: health_module.HealthComponent(status="ok", message="duckdb"),
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": {"status": "ok", "message": "duckdb"},
    }


def test_health_returns_503_when_dependency_is_down(monkeypatch) -> None:
    monkeypatch.setattr(
        health_module,
        "check_database",
        lambda: health_module.HealthComponent(status="error", message="connection refused"),
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == {"status": "error", "message": "connection refused"}


def test_openapi_contains_v1_routes() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/securities" in paths
    assert "/api/v1/daily-bars/{symbol}" in paths
    assert "/api/v1/daily-bars/{symbol}/chart" in paths
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
    assert "/api/v1/factors/batch-profiles" in paths
    assert "/api/v1/factors/batch-profiles/summary" in paths
    assert "/api/v1/factors/pool-classifications" in paths
    assert "/api/v1/backtests/runs" in paths
    assert "/api/v1/backtests/runs/{run_id}" in paths
    assert "/api/v1/backtests/runs/{run_id}/trades" in paths
    assert "/api/v1/backtests/runs/{run_id}/equity" in paths
    assert "/api/v1/backtests/runs/{run_id}/positions" in paths
    assert "/api/v1/backtests/runs/{run_id}/performance" in paths
    assert "/api/v1/backtests/runs/{run_id}/benchmark" in paths
    assert "/api/v1/backtests/runs/compare" in paths
    assert "/api/v1/backtests/runs/validation-report" in paths
    assert "/api/v1/strategy-research/factor-selection/backtests" in paths
    assert "/api/v1/strategy-research/factor-selection/backtests/batch" in paths
    assert "/api/v1/strategy-research/rolling-windows" in paths
    assert "/api/v1/backtests/runs/drift" in paths
    assert "/api/v1/operations/overview" in paths
    assert "/api/v1/operations/alerts" in paths
    assert "/api/v1/operations/failures" in paths
    assert "/api/v1/operations/failures/{failure_type}/{failure_id}" in paths
    assert "/api/v1/operations/failures/{failure_type}/{failure_id}/rerun-plan" in paths
    assert "/api/v1/portfolio/plans" in paths
    assert "/api/v1/portfolio/plans/{plan_id}" in paths
    assert "/api/v1/portfolio/plans/{plan_id}/risk-readiness" in paths
    assert "/api/v1/portfolio/plans/{plan_id}/risk-evaluations" in paths
    assert "/api/v1/paper-accounts" in paths
    assert "/api/v1/paper-accounts/{account_id}/equity-snapshots" in paths
    assert "/api/v1/paper-accounts/{account_id}/equity-snapshots/{snapshot_id}" in paths
    assert "/api/v1/risk-evaluations/{risk_check_id}/confirmations" in paths
    assert "/api/v1/paper-execution-approvals/{approval_id}" in paths
    assert "/api/v1/paper-execution-approvals/{approval_id}/revoke" in paths
    assert "/api/v1/paper-execution-approvals/{approval_id}/executions" in paths
    assert "/api/v1/paper-execution-approvals/{approval_id}/execution" in paths
    assert "/api/v1/paper-executions/{execution_id}" in paths


def test_list_chart_daily_bars_uses_latest_consumable_contract(monkeypatch) -> None:
    captured: dict = {}

    def fake_list(db, **kwargs):
        captured.update(kwargs)
        return {
            "items": [
                {
                    "symbol": "000001.SZ",
                    "security_type": "stock",
                    "trade_date": "2026-07-17",
                    "adjust_type": "none",
                    "open": "10.00",
                    "high": "10.80",
                    "low": "9.90",
                    "close": "10.50",
                    "pre_close": "10.00",
                    "change_amount": "0.50",
                    "pct_change": "5.00",
                    "volume": "1000000",
                    "amount": "10500000",
                    "turnover_rate": "1.25",
                    "amplitude": "9.00",
                    "source_name": "test",
                    "source_count": 1,
                    "quality_score": "1.00",
                    "quality_status": "manual_fixed",
                    "is_suspect": False,
                    "updated_at": datetime(2026, 7, 17, tzinfo=UTC).isoformat(),
                }
            ],
            "total": 1,
            "limit": 320,
            "offset": 0,
        }

    monkeypatch.setattr(daily_bars_module.service, "list_chart_daily_bars", fake_list)
    response = client.get(
        "/api/v1/daily-bars/000001.sz/chart",
        params={
            "adjust_type": "none",
            "start_date": "2026-01-01",
            "end_date": "2026-07-17",
            "limit": 320,
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["quality_status"] == "manual_fixed"
    assert captured == {
        "symbol": "000001.sz",
        "adjust_type": "none",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 7, 17),
        "limit": 320,
    }


def test_list_daily_bars_accepts_consumable_quality_scope(monkeypatch) -> None:
    captured: dict = {}

    def fake_list(db, **kwargs):
        captured.update(kwargs)
        return {"items": [], "total": 0, "limit": 1000, "offset": 0}

    monkeypatch.setattr(daily_bars_module.service, "list_daily_bars", fake_list)
    response = client.get(
        "/api/v1/daily-bars/000001.SZ",
        params={"adjust_type": "none", "quality_status": "consumable"},
    )

    assert response.status_code == 200
    assert captured["quality_status"] == "consumable"


def test_chart_daily_bars_rejects_invalid_date_range() -> None:
    response = client.get(
        "/api/v1/daily-bars/000001.SZ/chart",
        params={"start_date": "2026-07-18", "end_date": "2026-07-17"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def _portfolio_plan_response() -> dict:
    return {
        "plan_id": 11,
        "name": "draft",
        "as_of_date": "2026-07-21",
        "status": "draft",
        "execution_allowed": False,
        "input_source": "operator_provided",
        "total_equity": "100000",
        "cash_before": "100000",
        "target_cash": "5000",
        "cash_after": "80000",
        "constraints": {
            "cash_reserve_rate": "0.05",
            "max_symbol_weight": "0.20",
            "max_industry_weight": "0.40",
            "security_type_budgets": {},
        },
        "code_version": "test",
        "created_at": datetime(2026, 7, 21, tzinfo=UTC).isoformat(),
        "targets": [
            {
                "symbol": "600519.SH",
                "security_type": "stock",
                "industry": "食品饮料",
                "requested_weight": "0.30",
                "constrained_weight": "0.20",
                "final_weight": "0.20",
                "current_quantity": 0,
                "target_quantity": 100,
                "order_quantity": 100,
                "price": "200",
                "lot_size": 100,
                "reasons": ["max_symbol_weight"],
            }
        ],
    }


def test_create_portfolio_plan_forwards_operator_draft(monkeypatch) -> None:
    captured: dict = {}

    def fake_build(db, **kwargs):
        captured.update(kwargs)
        return _portfolio_plan_response()

    monkeypatch.setattr(portfolio_module.service, "build", fake_build)
    response = client.post(
        "/api/v1/portfolio/plans",
        headers={"Idempotency-Key": "draft-11"},
        json={
            "name": "draft",
            "as_of_date": "2026-07-21",
            "cash": "100000",
            "assets": [
                {
                    "symbol": "600519.sh",
                    "security_type": "stock",
                    "industry": "食品饮料",
                    "price": "200",
                    "current_quantity": 0,
                    "requested_weight": "0.30",
                    "lot_size": 100,
                }
            ],
        },
    )

    assert response.status_code == 201
    assert response.json()["execution_allowed"] is False
    assert captured["idempotency_key"] == "draft-11"
    assert captured["assets"][0].symbol == "600519.SH"
    assert captured["input_snapshot"]["input_source"] == "operator_provided"


def test_create_portfolio_plan_requires_idempotency_key() -> None:
    response = client.post(
        "/api/v1/portfolio/plans",
        json={
            "name": "draft",
            "as_of_date": "2026-07-21",
            "cash": "100000",
            "assets": [],
        },
    )

    assert response.status_code == 422


def test_get_portfolio_plan_maps_missing_to_404(monkeypatch) -> None:
    monkeypatch.setattr(portfolio_module.service, "get_plan", lambda db, **kwargs: None)

    response = client.get("/api/v1/portfolio/plans/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "portfolio plan not found"


def test_get_portfolio_plan_returns_saved_draft(monkeypatch) -> None:
    monkeypatch.setattr(
        portfolio_module.service,
        "get_plan",
        lambda db, **kwargs: _portfolio_plan_response(),
    )

    response = client.get("/api/v1/portfolio/plans/11")

    assert response.status_code == 200
    assert response.json()["plan_id"] == 11
    assert response.json()["status"] == "draft"


def test_create_portfolio_plan_maps_idempotency_conflict_to_409(monkeypatch) -> None:
    def fake_build(db, **kwargs):
        raise PortfolioIdempotencyConflict(
            "idempotency key was already used with a different request"
        )

    monkeypatch.setattr(portfolio_module.service, "build", fake_build)
    response = client.post(
        "/api/v1/portfolio/plans",
        headers={"Idempotency-Key": "draft-11"},
        json={
            "name": "changed draft",
            "as_of_date": "2026-07-21",
            "cash": "100000",
            "assets": [
                {
                    "symbol": "600519.SH",
                    "security_type": "stock",
                    "industry": "食品饮料",
                    "price": "200",
                    "current_quantity": 0,
                    "requested_weight": "0.10",
                }
            ],
        },
    )

    assert response.status_code == 409
    assert "different request" in response.json()["detail"]


def _portfolio_readiness_response() -> dict:
    return {
        "plan_id": 11,
        "paper_account_id": 1,
        "readiness_stage": "pre_risk",
        "ready_for_risk_check": True,
        "execution_allowed": False,
        "risk_evaluated": False,
        "orders_created": False,
        "read_only": True,
        "access_scope": "local_single_user",
        "consistency_level": "read_committed_diagnostic",
        "checked_at": datetime(2026, 7, 21, tzinfo=UTC).isoformat(),
        "account_snapshot_hash": "a" * 64,
        "market_snapshot_hash": "b" * 64,
        "checks": [],
        "blockers": [],
    }


def test_get_portfolio_risk_readiness_remains_non_executable(monkeypatch) -> None:
    captured: dict = {}

    def fake_check(db, **kwargs):
        captured.update(kwargs)
        return _portfolio_readiness_response()

    monkeypatch.setattr(portfolio_module.readiness_service, "check", fake_check)
    response = client.get(
        "/api/v1/portfolio/plans/11/risk-readiness",
        params={"paper_account_id": 1},
    )

    assert response.status_code == 200
    assert response.json()["ready_for_risk_check"] is True
    assert response.json()["execution_allowed"] is False
    assert response.json()["risk_evaluated"] is False
    assert response.json()["orders_created"] is False
    assert captured == {"plan_id": 11, "paper_account_id": 1}


def test_get_portfolio_risk_readiness_maps_missing_plan_to_404(monkeypatch) -> None:
    def fake_check(db, **kwargs):
        raise PortfolioPlanNotFound("portfolio plan not found")

    monkeypatch.setattr(portfolio_module.readiness_service, "check", fake_check)
    response = client.get(
        "/api/v1/portfolio/plans/999/risk-readiness",
        params={"paper_account_id": 1},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "portfolio plan not found"


def test_get_portfolio_risk_readiness_rejects_invalid_account_id() -> None:
    response = client.get(
        "/api/v1/portfolio/plans/11/risk-readiness",
        params={"paper_account_id": 0},
    )

    assert response.status_code == 422


def _paper_equity_snapshot_response() -> dict:
    return {
        "snapshot_id": 21,
        "account_id": 1,
        "valuation_date": "2026-07-21",
        "cash_balance": "10000",
        "positions_market_value": "0",
        "total_equity": "10000",
        "observed_high_watermark": "10000",
        "drawdown_since_monitoring_start": "0",
        "previous_snapshot_id": None,
        "monitoring_start_date": "2026-07-21",
        "observation_count": 1,
        "history_status": "bootstrap",
        "account_snapshot_hash": "a" * 64,
        "market_snapshot_hash": "b" * 64,
        "valuation_hash": "c" * 64,
        "valuation_policy": {
            "name": "paper_account_daily_close",
            "version": "v1",
            "adjust_type": "none",
            "accepted_quality": "trusted,manual_fixed",
            "price_field": "close",
            "history_scope": "since_monitoring_start",
        },
        "code_version": "test",
        "created_at": datetime(2026, 7, 21, tzinfo=UTC).isoformat(),
        "execution_allowed": False,
        "risk_evaluated": False,
        "orders_created": False,
        "access_scope": "local_single_user",
        "components": [],
    }


def test_list_paper_accounts_returns_selectable_accounts(monkeypatch) -> None:
    monkeypatch.setattr(
        paper_accounts_module.service,
        "list_accounts",
        lambda db: [{"id": 1, "name": "本地模拟账户", "cash_balance": "100000.0000"}],
    )

    response = client.get("/api/v1/paper-accounts")

    assert response.status_code == 200
    assert response.json() == [
        {"id": 1, "name": "本地模拟账户", "cash_balance": "100000.0000"}
    ]


def test_get_paper_ledger_audit_is_read_only(monkeypatch) -> None:
    captured: dict = {}

    def fake_audit(db, **kwargs):
        captured.update(kwargs)
        return {
            "account_id": 1,
            "account_name": "本地模拟账户",
            "status": "consistent",
            "actual_cash_balance": "100000.0000",
            "expected_cash_balance": "100000.0000",
            "cash_difference": "0.0000",
            "fill_count": 2,
            "completed_execution_batch_count": 1,
            "unlinked_fill_count": 0,
            "positions": [],
            "issues": [],
            "read_only": True,
            "auto_repair_performed": False,
        }

    monkeypatch.setattr(
        paper_accounts_module.audit_service,
        "audit_account",
        fake_audit,
    )

    response = client.get("/api/v1/paper-accounts/1/ledger-audit")

    assert response.status_code == 200
    assert response.json()["status"] == "consistent"
    assert response.json()["read_only"] is True
    assert response.json()["auto_repair_performed"] is False
    assert captured == {"account_id": 1}


def test_get_paper_ledger_audit_maps_missing_account_to_404(monkeypatch) -> None:
    def fake_audit(db, **kwargs):
        raise paper_accounts_module.PaperLedgerAccountNotFound("paper account not found")

    monkeypatch.setattr(
        paper_accounts_module.audit_service,
        "audit_account",
        fake_audit,
    )

    response = client.get("/api/v1/paper-accounts/999/ledger-audit")

    assert response.status_code == 404
    assert response.json()["detail"] == "paper account not found"


def test_capture_paper_equity_snapshot_forwards_only_date_and_account(monkeypatch) -> None:
    captured: dict = {}

    def fake_capture(db, **kwargs):
        captured.update(kwargs)
        return _paper_equity_snapshot_response()

    monkeypatch.setattr(paper_accounts_module.service, "capture", fake_capture)
    response = client.post(
        "/api/v1/paper-accounts/1/equity-snapshots",
        headers={"Idempotency-Key": "equity-21"},
        json={"valuation_date": "2026-07-21"},
    )

    assert response.status_code == 201
    assert response.json()["risk_evaluated"] is False
    assert captured == {
        "account_id": 1,
        "valuation_date": date(2026, 7, 21),
        "idempotency_key": "equity-21",
    }


def test_get_paper_equity_snapshot_maps_missing_to_404(monkeypatch) -> None:
    def fake_get(db, **kwargs):
        raise PaperEquitySnapshotNotFound("paper equity snapshot not found")

    monkeypatch.setattr(paper_accounts_module.service, "get_snapshot", fake_get)
    response = client.get("/api/v1/paper-accounts/1/equity-snapshots/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "paper equity snapshot not found"


def _risk_evaluation_response() -> dict:
    return {
        "check_id": 31,
        "plan_id": 11,
        "paper_account_id": 1,
        "equity_snapshot_id": 21,
        "evaluation_status": "passed",
        "risk_passed": True,
        "current_drawdown": "-0.02",
        "limits": {
            "max_symbol_weight": "0.20",
            "max_industry_weight": "0.40",
            "max_drawdown": "0.10",
            "max_amount_participation": "0.10",
        },
        "preconditions": [],
        "plan_request_hash": "p" * 64,
        "readiness_account_hash": "a" * 64,
        "readiness_market_hash": "m" * 64,
        "readiness_hash": "r" * 64,
        "adv_snapshot_hash": "d" * 64,
        "risk_policy_hash": "q" * 64,
        "evaluation_hash": "e" * 64,
        "code_version": "test",
        "created_at": datetime(2026, 7, 21, tzinfo=UTC).isoformat(),
        "risk_evaluated": True,
        "execution_allowed": False,
        "orders_created": False,
        "access_scope": "local_single_user",
        "orders": [],
    }


def test_create_bound_risk_evaluation_forwards_only_snapshot_refs(monkeypatch) -> None:
    captured: dict = {}

    def fake_evaluate(db, **kwargs):
        captured.update(kwargs)
        return _risk_evaluation_response()

    monkeypatch.setattr(
        portfolio_module.risk_evaluation_service,
        "evaluate",
        fake_evaluate,
    )
    response = client.post(
        "/api/v1/portfolio/plans/11/risk-evaluations",
        headers={"Idempotency-Key": "risk-31"},
        json={"paper_account_id": 1, "equity_snapshot_id": 21},
    )

    assert response.status_code == 201
    assert response.json()["risk_evaluated"] is True
    assert response.json()["execution_allowed"] is False
    assert response.json()["orders_created"] is False
    assert captured == {
        "plan_id": 11,
        "paper_account_id": 1,
        "equity_snapshot_id": 21,
        "idempotency_key": "risk-31",
    }


def _paper_execution_approval_response(*, status: str = "pending") -> dict:
    now = datetime(2026, 7, 21, tzinfo=UTC)
    return {
        "approval_id": 41,
        "risk_check_id": 31,
        "plan_id": 11,
        "account_id": 1,
        "equity_snapshot_id": 21,
        "stored_status": status,
        "effective_status": status,
        "approval_scope": "paper_execution",
        "actor_type": "local_single_user",
        "approved_by": "local_operator",
        "code_version": "test",
        "evaluation_hash": "e" * 64,
        "plan_request_hash": "p" * 64,
        "equity_valuation_hash": "v" * 64,
        "readiness_account_hash": "a" * 64,
        "readiness_market_hash": "m" * 64,
        "readiness_hash": "r" * 64,
        "adv_snapshot_hash": "d" * 64,
        "risk_policy_hash": "q" * 64,
        "order_batch_hash": "o" * 64,
        "approval_hash": "h" * 64,
        "order_summary": [],
        "confirmation_policy": {"execution_mode": "paper", "ttl_seconds": 300},
        "approved_at": now.isoformat(),
        "expires_at": now.replace(minute=5).isoformat(),
        "consumed_at": None,
        "revoked_at": now.isoformat() if status == "revoked" else None,
        "revocation_reason": "operator cancelled" if status == "revoked" else None,
        "created_at": now.isoformat(),
        "execution_allowed": False,
        "orders_created": False,
        "consumable_later": status == "pending",
        "access_scope": "local_single_user",
    }


def test_create_paper_execution_approval_is_confirmation_only(monkeypatch) -> None:
    captured: dict = {}

    def fake_create(db, **kwargs):
        captured.update(kwargs)
        return _paper_execution_approval_response()

    monkeypatch.setattr(approvals_module.service, "create", fake_create)
    response = client.post(
        "/api/v1/risk-evaluations/31/confirmations",
        headers={"Idempotency-Key": "approval-41"},
        json={
            "expected_evaluation_hash": "e" * 64,
            "confirmation_phrase": "CONFIRM PAPER EXECUTION 31",
        },
    )

    assert response.status_code == 201
    assert response.json()["execution_allowed"] is False
    assert response.json()["orders_created"] is False
    assert response.json()["consumable_later"] is True
    assert captured == {
        "risk_check_id": 31,
        "expected_evaluation_hash": "e" * 64,
        "confirmation_phrase": "CONFIRM PAPER EXECUTION 31",
        "idempotency_key": "approval-41",
    }


def test_paper_execution_approval_error_mappings(monkeypatch) -> None:
    cases = [
        (PaperApprovalNotFound("missing"), 404),
        (PaperApprovalConflict("stale"), 409),
        (PaperApprovalValidationError("bad phrase"), 422),
    ]
    for error, expected_status in cases:
        monkeypatch.setattr(
            approvals_module.service,
            "create",
            lambda db, error=error, **kwargs: (_ for _ in ()).throw(error),
        )
        response = client.post(
            "/api/v1/risk-evaluations/31/confirmations",
            headers={"Idempotency-Key": "approval-error"},
            json={
                "expected_evaluation_hash": "e" * 64,
                "confirmation_phrase": "CONFIRM PAPER EXECUTION 31",
            },
        )

        assert response.status_code == expected_status


def test_get_and_revoke_paper_execution_approval(monkeypatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(
        approvals_module.service,
        "get",
        lambda db, **kwargs: _paper_execution_approval_response(),
    )

    def fake_revoke(db, **kwargs):
        captured.update(kwargs)
        return _paper_execution_approval_response(status="revoked")

    monkeypatch.setattr(approvals_module.service, "revoke", fake_revoke)

    get_response = client.get("/api/v1/paper-execution-approvals/41")
    revoke_response = client.post(
        "/api/v1/paper-execution-approvals/41/revoke",
        headers={"Idempotency-Key": "revoke-41"},
        json={"reason": "operator cancelled"},
    )

    assert get_response.status_code == 200
    assert revoke_response.status_code == 200
    assert revoke_response.json()["effective_status"] == "revoked"
    assert revoke_response.json()["consumable_later"] is False
    assert captured == {
        "approval_id": 41,
        "reason": "operator cancelled",
        "idempotency_key": "revoke-41",
    }


def _paper_execution_response() -> dict:
    now = datetime(2026, 7, 21, tzinfo=UTC).isoformat()
    return {
        "execution_id": 51,
        "approval_id": 41,
        "risk_check_id": 31,
        "plan_id": 11,
        "account_id": 1,
        "equity_snapshot_id": 21,
        "status": "completed",
        "execution_mode": "paper",
        "actor_type": "local_single_user",
        "executed_by": "local_operator",
        "code_version": "test",
        "approval_hash": "h" * 64,
        "evaluation_hash": "e" * 64,
        "order_batch_hash": "o" * 64,
        "execution_policy_hash": "x" * 64,
        "account_state_hash_before": "a" * 64,
        "account_state_hash_after": "b" * 64,
        "execution_hash": "z" * 64,
        "cash_before": "1000",
        "cash_after": "900",
        "order_count": 1,
        "fill_count": 1,
        "total_commission": "0.3",
        "failure_code": None,
        "failure_message": None,
        "started_at": now,
        "completed_at": now,
        "created_at": now,
        "approval_status": "consumed",
        "real_broker_submitted": False,
        "next_step": "capture_new_equity_snapshot",
        "access_scope": "local_single_user",
        "orders": [],
    }


def test_execute_paper_approval_forwards_only_approval_hash(monkeypatch) -> None:
    captured: dict = {}

    def fake_execute(db, **kwargs):
        captured.update(kwargs)
        return _paper_execution_response()

    monkeypatch.setattr(approvals_module.execution_service, "execute", fake_execute)
    response = client.post(
        "/api/v1/paper-execution-approvals/41/executions",
        headers={"Idempotency-Key": "execute-51"},
        json={"expected_approval_hash": "h" * 64},
    )

    assert response.status_code == 201
    assert response.json()["execution_mode"] == "paper"
    assert response.json()["real_broker_submitted"] is False
    assert captured == {
        "approval_id": 41,
        "expected_approval_hash": "h" * 64,
        "idempotency_key": "execute-51",
    }


def test_get_paper_execution_maps_missing_to_404(monkeypatch) -> None:
    def fake_get(db, **kwargs):
        raise approvals_module.PaperExecutionNotFound("paper execution batch not found")

    monkeypatch.setattr(approvals_module.execution_service, "get", fake_get)
    response = client.get("/api/v1/paper-executions/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "paper execution batch not found"


def test_get_paper_execution_by_approval(monkeypatch) -> None:
    captured: dict = {}

    def fake_get(db, **kwargs):
        captured.update(kwargs)
        return _paper_execution_response()

    monkeypatch.setattr(approvals_module.execution_service, "get_by_approval", fake_get)
    response = client.get("/api/v1/paper-execution-approvals/41/execution")

    assert response.status_code == 200
    assert captured == {"approval_id": 41}


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

    def fake_get_alerts(db, **kwargs):
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
                    "rule": "backtest runs with status=failed",
                    "next_action": "inspect run detail and error message",
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
    captured.pop("research_db", None)  # session object, not part of the assertion
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
                "window_id": "rolling:SH:2026-01-02:2026-01-08:2026-01-09:2026-01-13",
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
    assert response.json()[0]["execution_status"] == "planning_only"
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


def test_validation_report_maps_incompatible_runs_to_422(monkeypatch) -> None:
    captured: dict = {}

    def fake_build(db, **kwargs):
        captured.update(kwargs)
        raise ValueError("validation runs have incompatible contract fields: top_n")

    monkeypatch.setattr(
        backtests_module.validation_report_service,
        "build_report",
        fake_build,
    )
    response = client.post(
        "/api/v1/backtests/runs/validation-report",
        json={"run_ids": [1, 2]},
    )

    assert response.status_code == 422
    assert response.json()["detail"].endswith("top_n")
    assert captured["run_ids"] == [1, 2]


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
            quantile=None,
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


def test_rolling_batch_rejects_pseudo_walk_forward_execution() -> None:
    base_run = {
        "factor_specs": [{"factor_name": "momentum_20", "factor_version": "v1-qfq"}],
        "top_n": 1,
        "initial_cash": "100000",
        "code_version": "abc123",
        "data_version": "market-qfq-20260720",
        "validation_mode": "rolling",
    }
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests/batch",
        json={
            "runs": [
                {
                    **base_run,
                    "name": "rolling-0",
                    "training_start_date": "2026-01-02",
                    "training_end_date": "2026-06-30",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-10",
                },
                {
                    **base_run,
                    "name": "rolling-1",
                    "training_start_date": "2026-01-12",
                    "training_end_date": "2026-07-10",
                    "start_date": "2026-07-13",
                    "end_date": "2026-07-22",
                },
            ]
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "rolling execution must use POST /strategy-research/rolling-backtests; "
        "the batch endpoint does not accept validation_mode=rolling"
    )


def test_single_rolling_run_rejects_pseudo_walk_forward_execution() -> None:
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests",
        json={
            "name": "pseudo-rolling",
            "factor_specs": [
                {"factor_name": "momentum_20", "factor_version": "v1-qfq"}
            ],
            "top_n": 1,
            "start_date": "2026-07-01",
            "end_date": "2026-07-10",
            "initial_cash": "100000",
            "code_version": "abc123",
            "data_version": "market-qfq-20260720",
            "validation_mode": "rolling",
            "training_start_date": "2026-01-02",
            "training_end_date": "2026-06-30",
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "rolling execution must use run_rolling_factor_selection; "
        "single backtest requests cannot bind a validation window"
    )


def test_run_batch_factor_profiles_forwards_filters(monkeypatch) -> None:
    captured: dict = {}

    def fake_run(db, **kwargs):
        captured.update(kwargs)
        return FactorBatchProfilerRunResult(
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 17),
            adjust_type="qfq",
            results=[
                FactorProfileResult(
                    factor_name="ret_1d",
                    factor_version="v1-qfq",
                    category="price_volume_risk",
                    success=True,
                    profile_rows=12,
                    transformed_rows=48,
                    run_ids={"winsor": 1},
                )
            ],
            total_profile_rows=12,
            total_transformed_rows=48,
            success_count=1,
            failed_count=0,
        )

    monkeypatch.setattr(factors_module.batch_profiler_service, "run", fake_run)

    response = client.post(
        "/api/v1/factors/batch-profiles",
        json={
            "start_date": "2026-07-01",
            "end_date": "2026-07-17",
            "adjust_type": "qfq",
            "categories": ["price_volume_risk"],
            "excluded_categories": ["crowding"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["success_count"] == 1
    assert body["total_transformed_rows"] == 48
    assert body["results"][0]["factor_name"] == "ret_1d"
    assert captured["categories"] == {"price_volume_risk"}
    assert captured["excluded_categories"] == {"crowding"}
    assert captured["adjust_type"] == "qfq"
    assert captured["size_factor_name"] == "market_cap"
    assert captured["size_factor_version"] == "v1"


def test_run_batch_factor_profiles_rejects_reversed_range() -> None:
    response = client.post(
        "/api/v1/factors/batch-profiles",
        json={"start_date": "2026-07-17", "end_date": "2026-07-01"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"


def test_list_factor_profile_summaries_forwards_filters(monkeypatch) -> None:
    captured: dict = {}

    def fake_summarize(db, **kwargs):
        captured.update(kwargs)
        return (
            [
                FactorProfileSummary(
                    factor_name="ret_1d",
                    factor_version="v1-qfq",
                    category="price_volume_risk",
                    trade_date_count=10,
                    first_trade_date=date(2026, 7, 1),
                    last_trade_date=date(2026, 7, 17),
                    mean_ic_1d=Decimal("0.03"),
                    mean_rank_ic_1d=Decimal("0.04"),
                )
            ],
            1,
        )

    monkeypatch.setattr(
        factors_module.batch_profiler_service, "summarize", fake_summarize
    )

    response = client.get(
        "/api/v1/factors/batch-profiles/summary",
        params={"factor_name": "ret_1d", "limit": 5, "offset": 0},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["has_ic_evidence"] is True
    assert body["items"][0]["mean_ic_1d"] == "0.03"
    assert captured["factor_name"] == "ret_1d"
    assert captured["limit"] == 5


def test_classify_factor_pool_forwards_thresholds(monkeypatch) -> None:
    from app.services.factor_batch_profiler import FactorProfileSummary

    captured_query: dict = {}

    def fake_summarize(db, **kwargs):
        captured_query.update(kwargs)
        return (
            [
                FactorProfileSummary(
                    factor_name="ret_1d",
                    factor_version="v1-qfq",
                    category="price_volume_risk",
                    trade_date_count=250,
                    first_trade_date=date(2024, 1, 2),
                    last_trade_date=date(2025, 12, 31),
                    mean_coverage_rate=Decimal("0.90"),
                    mean_rank_ic_1d=Decimal("0.02"),
                    mean_rank_ic_5d=Decimal("0.02"),
                    mean_rank_ic_20d=Decimal("0.02"),
                )
            ],
            1,
        )

    monkeypatch.setattr(
        factors_module.batch_profiler_service, "summarize", fake_summarize
    )

    response = client.post(
        "/api/v1/factors/pool-classifications",
        json={
            "start_date": "2024-01-02",
            "end_date": "2025-12-31",
            "min_trade_dates": 100,
            "min_coverage_rate": "0.80",
            "min_rank_ic_1d": "0.015",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["pool"] == "core"
    assert body[0]["category"] == "price_volume_risk"
    assert captured_query["start_date"] == date(2024, 1, 2)
    assert captured_query["end_date"] == date(2025, 12, 31)


def test_classify_factor_pool_rejects_reversed_range() -> None:
    response = client.post(
        "/api/v1/factors/pool-classifications",
        json={"start_date": "2025-12-31", "end_date": "2024-01-02"},
    )

    assert response.status_code == 422
