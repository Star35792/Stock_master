"""研究流水线聚合服务（BFF 层）。

把散落在细粒度路由背后的能力，按量化使用者的研究心智串成两个聚合视图：

* ``workbench`` —— 一次返回「最近回测 + 组合草案 + 持仓账本 + 运维告警」，
  替代前端首屏要打的 4+ 个接口。
* ``pipeline`` —— 给定一个回测 ``run_id``，一次返回「运行摘要 → 净值曲线 →
  绩效 → 基准对比 → 期末持仓」，把现在 6 次跳转压缩成 1 次请求。

实现约束：
* 只编排，不写 SQL、不直接访问 repository 之外的数据层。
* 复用 ``BacktestQueryService`` / ``OperationsOverviewService`` /
  ``PortfolioConstructionService`` / ``PositionBookService`` 的既有方法。
* 每个子模块独立容错：单个数据源缺失时降级为 ``None`` 而非整体失败，
  与「单源失败不拖垮其他源」的项目口径一致。
* 本服务是只读研究视图，不写库、不触发 PIT/对账等硬约束。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.backtest_query import BacktestQueryService
from app.services.operations import OperationsOverviewService
from app.services.portfolio import PortfolioConstructionService
from app.services.position_book import PositionBookService


class ResearchWorkflowService:
    """Aggregate read-only views for the research pipeline."""

    def __init__(
        self,
        backtest_query: BacktestQueryService | None = None,
        operations: OperationsOverviewService | None = None,
        portfolio: PortfolioConstructionService | None = None,
        position_book: PositionBookService | None = None,
    ) -> None:
        self.backtest_query = backtest_query or BacktestQueryService()
        self.operations = operations or OperationsOverviewService()
        self.portfolio = portfolio or PortfolioConstructionService()
        self.position_book = position_book or PositionBookService()

    def workbench(
        self,
        db: Session,
        *,
        positions_db: Session,
        research_db: Session | None = None,
        recent_run_limit: int = 5,
        recent_plan_limit: int = 10,
    ) -> dict[str, Any]:
        """Aggregate the research landing page in one round-trip.

        Each section is independently best-effort: a failing section degrades
        to ``None`` with an ``error`` string instead of aborting the response.
        """
        sections: dict[str, Any] = {
            "recent_backtests": None,
            "recent_backtests_error": None,
            "portfolio_plans": None,
            "portfolio_plans_error": None,
            "position_books": None,
            "position_books_error": None,
            "alerts": None,
            "alerts_error": None,
        }

        # 最近回测（成功 + 运行中，按时间倒序由 list_runs 保证？这里显式
        # 取 latest，交由既有 list_runs 的分页与排序语义）。
        try:
            runs = self.backtest_query.list_runs(
                research_db if research_db is not None else db,
                limit=recent_run_limit,
                offset=0,
            )
            sections["recent_backtests"] = [item.model_dump(mode="json") for item in runs.items]
        except Exception as exc:  # noqa: BLE001 — 聚合层容错，逐段降级
            sections["recent_backtests_error"] = str(exc)

        # 组合草案列表。
        try:
            plans = self.portfolio.list_plans(db, limit=recent_plan_limit, offset=0)
            sections["portfolio_plans"] = [item.model_dump(mode="json") for item in plans.items]
        except Exception as exc:  # noqa: BLE001
            sections["portfolio_plans_error"] = str(exc)

        # 持仓账本列表。
        try:
            books = self.position_book.list_books(positions_db)
            sections["position_books"] = [item.model_dump(mode="json") for item in books]
        except Exception as exc:  # noqa: BLE001
            sections["position_books_error"] = str(exc)

        # 运维告警摘要。
        try:
            alerts = self.operations.get_alerts(db, research_db=research_db)
            sections["alerts"] = alerts.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001
            sections["alerts_error"] = str(exc)

        return sections

    def pipeline(
        self,
        db: Session,
        *,
        run_id: int,
        research_db: Session | None = None,
    ) -> dict[str, Any]:
        """Aggregate a single backtest run into a full research view.

        Sections degrade independently; ``run`` itself is the only hard
        prerequisite (a missing run raises ``ValueError`` to map to 404).
        """
        artifacts_db = research_db if research_db is not None else db

        run = self.backtest_query.get_run(artifacts_db, run_id=run_id)
        if run is None:
            raise ValueError("backtest run not found")

        result: dict[str, Any] = {
            "run": run.model_dump(mode="json"),
            "equity_curve": None,
            "equity_curve_error": None,
            "performance": None,
            "performance_error": None,
            "benchmark": None,
            "benchmark_error": None,
            "final_holdings": None,
            "final_holdings_error": None,
        }

        # 净值曲线。
        try:
            equity = self.backtest_query.list_equity(
                artifacts_db, run_id=run_id, limit=5000, offset=0
            )
            result["equity_curve"] = [item.model_dump(mode="json") for item in equity.items]
        except Exception as exc:  # noqa: BLE001
            result["equity_curve_error"] = str(exc)

        # 绩效指标。
        try:
            performance = self.backtest_query.get_performance(artifacts_db, run_id=run_id)
            result["performance"] = (
                performance.model_dump(mode="json") if performance is not None else None
            )
        except Exception as exc:  # noqa: BLE001
            result["performance_error"] = str(exc)

        # 基准对比（仅在回测配置了基准时才可能有值）。
        if result["run"].get("parameters", {}).get("benchmark"):
            try:
                benchmark = self.backtest_query.get_benchmark_comparison(
                    db, run_id=run_id, research_db=research_db
                )
                result["benchmark"] = benchmark.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                result["benchmark_error"] = str(exc)

        # 期末持仓（组合草案预填源）。
        try:
            holdings = self.backtest_query.get_final_holdings(
                db, run_id=run_id, research_db=research_db
            )
            result["final_holdings"] = (
                holdings.model_dump(mode="json") if holdings is not None else None
            )
        except Exception as exc:  # noqa: BLE001
            result["final_holdings_error"] = str(exc)

        return result
