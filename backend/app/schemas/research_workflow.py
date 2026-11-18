"""研究流水线聚合视图的响应模型（BFF 层）。

这些模型只描述聚合编排的对外形态，不新增数据库字段；字段来源都是
既有 ``app.schemas`` 中定义的模型，聚合时以 ``mode="json"`` 落进
``Any`` 槽位，避免在此处复制一份易漂移的字段清单。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkbenchRead(BaseModel):
    """研究首页的一次性聚合视图。

    每个子模块都是独立 best-effort：失败时对应槽位为 ``None``，并由
    ``*_error`` 字段携带原因，不因单点缺失拖垮整页。
    """

    recent_backtests: list[dict[str, Any]] | None = None
    recent_backtests_error: str | None = None
    portfolio_plans: list[dict[str, Any]] | None = None
    portfolio_plans_error: str | None = None
    position_books: list[dict[str, Any]] | None = None
    position_books_error: str | None = None
    alerts: dict[str, Any] | None = None
    alerts_error: str | None = None


class PipelineRead(BaseModel):
    """单次回测的完整研究视图。

    ``run`` 是硬前置条件（缺失 → 404）；其余子模块独立降级。
    """

    run: dict[str, Any] = Field(description="回测运行摘要")
    equity_curve: list[dict[str, Any]] | None = None
    equity_curve_error: str | None = None
    performance: dict[str, Any] | None = None
    performance_error: str | None = None
    benchmark: dict[str, Any] | None = None
    benchmark_error: str | None = None
    final_holdings: dict[str, Any] | None = None
    final_holdings_error: str | None = None
