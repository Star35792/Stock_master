"""研究流水线路由的装配与路径契约测试。

用独立 FastAPI 实例挂载 research_workflow router，mock 聚合 service，
验证路由路径、404 语义与响应形状，不触真实数据库。
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.research_workflow import router


def _make_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_research_workflow_routes_are_registered() -> None:
    """两条聚合路由都挂在 /api/v1/research-workflow 下。"""
    client = _make_client()
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/research-workflow/workbench" in paths
    assert "/api/v1/research-workflow/pipeline" in paths


def test_pipeline_returns_404_on_missing_run() -> None:
    """pipeline 在 run 缺失时返回 404（硬前置条件）。"""
    with patch(
        "app.services.research_workflow.ResearchWorkflowService.pipeline",
        side_effect=ValueError("backtest run not found"),
    ):
        client = _make_client()
        response = client.get("/api/v1/research-workflow/pipeline", params={"run_id": 1})
    assert response.status_code == 404
    assert response.json()["detail"] == "backtest run not found"


def test_workbench_accepts_limit_params_and_shape() -> None:
    """workbench 接受分页上限参数并返回聚合模型形状。"""
    with patch(
        "app.services.research_workflow.ResearchWorkflowService.workbench",
        return_value={
            "recent_backtests": [],
            "recent_backtests_error": None,
            "portfolio_plans": [],
            "portfolio_plans_error": None,
            "position_books": [],
            "position_books_error": None,
            "alerts": None,
            "alerts_error": None,
        },
    ):
        client = _make_client()
        response = client.get(
            "/api/v1/research-workflow/workbench",
            params={"recent_run_limit": 3, "recent_plan_limit": 7},
        )
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "recent_backtests",
        "recent_backtests_error",
        "portfolio_plans",
        "portfolio_plans_error",
        "position_books",
        "position_books_error",
        "alerts",
        "alerts_error",
    }
