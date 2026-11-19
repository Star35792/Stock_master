"""ResearchWorkflowService 聚合逻辑的单元测试。

只验证编排与逐段容错，不触真实数据库：四个子服务全部用 fake 注入，
覆盖「正常聚合」与「单段失败降级」两条路径。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.research_workflow import ResearchWorkflowService


class _FakeRun:
    def model_dump(self, mode: str = "python") -> dict:
        return {
            "id": 1, "name": "smoke", "status": "success",
            "parameters": {"benchmark": "000300.SH"},
        }


class _FakeItem:
    def __init__(self, value: dict) -> None:
        self._value = value

    def model_dump(self, mode: str = "python") -> dict:
        return dict(self._value)


class FakeBacktestQuery:
    def __init__(self) -> None:
        self.fail_equity = False

    def get_run(self, db, *, run_id):
        return _FakeRun()

    def list_runs(self, db, *, limit, offset):
        page = SimpleNamespace(items=[_FakeItem({"id": 1})], total=1)
        return page

    def list_equity(self, db, *, run_id, limit, offset):
        if self.fail_equity:
            raise RuntimeError("equity boom")
        return SimpleNamespace(items=[_FakeItem({"trade_date": "2026-07-01"})], total=1)

    def get_performance(self, db, *, run_id):
        return _FakeItem({"sharpe_ratio": "1.2"})

    def get_benchmark_comparison(self, db, *, run_id, research_db=None):
        return _FakeItem({"benchmark": "000300.SH"})

    def get_final_holdings(self, db, *, run_id, research_db=None):
        return _FakeItem({"run_id": 1, "holdings": []})


class FakeOperations:
    def get_alerts(self, db, research_db=None):
        return _FakeItem({"items": []})


class FakePortfolio:
    def list_plans(self, db, *, limit, offset):
        return SimpleNamespace(items=[_FakeItem({"plan_id": 1})], total=1)


class FakePositionBook:
    def __init__(self) -> None:
        self.last_db = None

    def list_books(self, db):
        self.last_db = db
        return [_FakeItem({"id": 1, "name": "book"})]


def _make_service():
    return ResearchWorkflowService(
        backtest_query=FakeBacktestQuery(),
        operations=FakeOperations(),
        portfolio=FakePortfolio(),
        position_book=FakePositionBook(),
    )


def test_pipeline_aggregates_all_sections():
    service = _make_service()
    result = service.pipeline(SimpleNamespace(), run_id=1, research_db=None)

    assert result["run"]["id"] == 1
    assert result["equity_curve"] is not None
    assert result["performance"]["sharpe_ratio"] == "1.2"
    assert result["benchmark"]["benchmark"] == "000300.SH"
    assert result["final_holdings"]["run_id"] == 1
    # 无失败时，error 槽位应为 None。
    assert result["equity_curve_error"] is None
    assert result["performance_error"] is None
    assert result["benchmark_error"] is None
    assert result["final_holdings_error"] is None


def test_pipeline_missing_run_raises_value_error():
    class _NoRunBacktestQuery:
        def get_run(self, db, *, run_id):
            return None

    service = ResearchWorkflowService(backtest_query=_NoRunBacktestQuery())
    try:
        service.pipeline(SimpleNamespace(), run_id=999, research_db=None)
    except ValueError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for missing run")


def test_pipeline_section_failure_degrades_independently():
    backtest = FakeBacktestQuery()
    backtest.fail_equity = True
    service = ResearchWorkflowService(backtest_query=backtest)

    result = service.pipeline(SimpleNamespace(), run_id=1, research_db=None)

    # 净值曲线段失败 → 降级为 None 并记录原因，其余段照常返回。
    assert result["equity_curve"] is None
    assert result["equity_curve_error"] == "equity boom"
    assert result["performance"] is not None
    assert result["benchmark"] is not None


def test_workbench_section_failure_degrades_independently():
    class _FailingPortfolio:
        def list_plans(self, db, *, limit, offset):
            raise RuntimeError("portfolio boom")

    position_book = FakePositionBook()
    service = ResearchWorkflowService(
        backtest_query=FakeBacktestQuery(),
        operations=FakeOperations(),
        portfolio=_FailingPortfolio(),
        position_book=position_book,
    )
    main_db = SimpleNamespace(name="main")
    positions_db = SimpleNamespace(name="positions")
    result = service.workbench(
        main_db,
        positions_db=positions_db,
        research_db=None,
    )

    assert result["recent_backtests"] is not None
    assert result["portfolio_plans"] is None
    assert result["portfolio_plans_error"] == "portfolio boom"
    assert result["position_books"] is not None
    assert position_book.last_db is positions_db
    assert result["alerts"] is not None
