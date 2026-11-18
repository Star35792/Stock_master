"""Production-shaped API regression: market and artifacts never share a database."""

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.routes import strategy_research as strategy_research_routes
from app.api.routes.backtests import router as backtests_router
from app.api.routes.operations import router as operations_router
from app.api.routes.strategy_research import router as research_router
from app.database import research_session as research_session_module
from app.database.research_session import get_research_db, initialize_research_database
from app.database.session import get_db, initialize_database
from app.models.market import DailyBar, Security, TradingCalendar
from app.models.strategy import BacktestRun
from app.repositories.strategy import BacktestRepository
from app.services.backtest import DailyBacktestService
from app.services.strategy_research import StrategyResearchService
from app.utils.duckdb_write_lock import DuckDbWriteLockTimeout, duckdb_write_lock
from tests.test_price_value_backtest_service import backtest_payload


@pytest.fixture
def split_api(tmp_path):
    market = create_engine(f"duckdb:///{tmp_path / 'market_core.duckdb'}")
    research = create_engine(f"duckdb:///{tmp_path / 'research.duckdb'}")
    initialize_database(market)
    initialize_research_database(research)
    with Session(market) as db:
        # The research *artifacts* live in their own database file.  The core
        # database may hold the strategy *definition* catalog, which is a
        # configuration table read on every call, not a run artifact.
        assert db.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='strategy' AND table_name IN ("
            "'backtest_run', 'backtest_trade', 'backtest_equity', "
            "'backtest_position', 'backtest_performance', 'training_artifact')"
        )) == 0
        db.add(Security(
            symbol="600001.SH", name="Fixture", security_type="stock", exchange="SH",
            currency="CNY", industry="食品饮料", is_active=True, list_date=date(2000, 1, 1),
        ))
        for offset, close in enumerate((6, 8, 13, 13, 13)):
            day = date(2026, 1, 5 + offset)
            db.add(TradingCalendar(exchange="SH", trade_date=day, is_open=True))
            db.add(DailyBar(
                symbol="600001.SH", security_type="stock", trade_date=day,
                adjust_type="none", open=Decimal(close), close=Decimal(close),
                high=Decimal(close), low=Decimal(close), volume=Decimal("100000"),
                amount=Decimal("1000000"), limit_up=Decimal("100"), limit_down=Decimal("1"),
                is_suspended=False, special_treatment_status="normal", source_name="fixture",
                quality_status="trusted", is_suspect=False,
            ))
        db.commit()

    def market_session():
        with Session(market) as db:
            yield db

    def research_session():
        with Session(research) as db:
            yield db

    app = FastAPI()
    for router in (research_router, backtests_router, operations_router):
        app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = market_session
    app.dependency_overrides[get_research_db] = research_session
    with TestClient(app) as client:
        yield client, market, research
    market.dispose()
    research.dispose()


def test_split_database_report_profit_benchmark_and_failure_plan(split_api):
    client, market, research = split_api
    root = "/api/v1/strategy-research/price-value"
    created = client.post(f"{root}/backtests", json=backtest_payload().model_dump(mode="json"))
    assert created.status_code == 200, created.text
    report = created.json()
    run_id = report["run_id"]
    fetched = client.get(f"{root}/backtests/{run_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == report
    profit = client.get(f"{root}/profit-status")
    assert profit.status_code == 200, profit.text
    assert profit.json()["value_backtest"]["run_id"] == run_id
    assert profit.json()["paper_accounts"] == []

    with Session(research) as db:
        run = db.get(BacktestRun, run_id)
        run.parameters = {**run.parameters, "benchmark": "600001.SH", "adjust_type": "none"}
        db.commit()
    benchmark = client.get(f"/api/v1/backtests/runs/{run_id}/benchmark")
    assert benchmark.status_code == 200, benchmark.text
    assert benchmark.json()["benchmark"] == "600001.SH"
    assert Decimal(benchmark.json()["benchmark_total_return"]) == Decimal(13) / Decimal(6) - 1

    before_analysis = client.get("/api/v1/backtests/runs", params={"limit": 20})
    assert before_analysis.status_code == 200, before_analysis.text
    listed = next(item for item in before_analysis.json()["items"] if item["id"] == run_id)
    assert listed["performance_status"] == "derived"
    assert listed["sharpe_ratio"] is None
    assert listed["performance_code_version"].startswith("source-")
    detail = client.get(f"/api/v1/backtests/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["performance_status"] == "derived"
    assert detail.json()["sharpe_ratio"] is None

    stale_version = client.post(
        f"/api/v1/backtests/runs/{run_id}/performance",
        json={"code_version": "old-client-version"},
    )
    assert stale_version.status_code == 422
    analyzed = client.post(f"/api/v1/backtests/runs/{run_id}/performance", json={})
    assert analyzed.status_code == 200, analyzed.text
    assert analyzed.json()["code_version"].startswith("source-")
    # A flat fixture equity curve has zero volatility, so Sharpe is undefined.
    assert analyzed.json()["sharpe_ratio"] is None
    fetched_performance = client.get(f"/api/v1/backtests/runs/{run_id}/performance")
    assert fetched_performance.json() == analyzed.json()
    repeated = client.post(f"/api/v1/backtests/runs/{run_id}/performance", json={})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json() == analyzed.json()
    after_analysis = client.get("/api/v1/backtests/runs", params={"limit": 20})
    listed = next(item for item in after_analysis.json()["items"] if item["id"] == run_id)
    assert listed["performance_status"] == "analyzed"
    assert listed["sharpe_ratio"] == analyzed.json()["sharpe_ratio"]

    with Session(research) as db:
        run = db.get(BacktestRun, run_id)
        run.status = "failed"
        run.error_message = "fixture failure"
        db.commit()
    plan = client.get(f"/api/v1/operations/failures/backtest_run/{run_id}/rerun-plan")
    assert plan.status_code == 200, plan.text
    assert plan.json()["failure_id"] == run_id
    assert plan.json()["action_type"] == "manual_review"
    with Session(market) as db:
        # The research *artifacts* live in their own database file.  The core
        # database may hold the strategy *definition* catalog, which is a
        # configuration table read on every call, not a run artifact.
        assert db.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='strategy' AND table_name IN ("
            "'backtest_run', 'backtest_trade', 'backtest_equity', "
            "'backtest_position', 'backtest_performance', 'training_artifact')"
        )) == 0


def test_compare_preview_reads_research_runs_without_saving_performance(split_api):
    client, _, research = split_api
    root = "/api/v1/strategy-research/price-value/backtests"
    first = client.post(root, json=backtest_payload().model_dump(mode="json"))
    second = client.post(root, json=backtest_payload().model_dump(mode="json"))
    assert first.status_code == second.status_code == 200
    run_ids = [second.json()["run_id"], first.json()["run_id"]]

    response = client.post(
        "/api/v1/backtests/runs/compare-preview", json={"run_ids": run_ids}
    )
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["run_id"] for item in items] == run_ids
    assert [item["metric_source"] for item in items] == ["derived", "derived"]
    assert all(item["metric_code_version"] for item in items)
    assert all(item["data_version"] for item in items)
    assert all(item["strategy_contract"]["strategy_name"] for item in items)
    assert items[0]["strategy_differences"] == []
    assert items[1]["strategy_differences"] == []
    assert all("signals" not in item["strategy_contract"] for item in items)
    with Session(research) as db:
        assert db.scalar(text("SELECT count(*) FROM strategy.backtest_performance")) == 0


def test_backtest_list_filters_validation_and_data_version_before_paging(split_api):
    client, _, research = split_api
    with Session(research) as db:
        for mode, version in (("rolling", "warm-a"), ("none", "warm-b"), ("rolling", "warm-b")):
            db.add(BacktestRun(
                name=f"{mode}-{version}", strategy_name="factor_selection",
                parameters={"validation_mode": mode}, code_version="fixture",
                data_version=version, start_date=date(2026, 1, 5),
                end_date=date(2026, 1, 9), initial_cash=Decimal("10000"),
                status="success",
            ))
        db.commit()

    response = client.get(
        "/api/v1/backtests/runs",
        params={"validation_mode": "rolling", "data_version": "warm-b", "limit": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [item["name"] for item in response.json()["items"]] == ["rolling-warm-b"]


def test_split_database_missing_report_is_404(split_api):
    client, _, _ = split_api
    for url in (
        "/api/v1/strategy-research/price-value/backtests/99999",
        "/api/v1/backtests/runs/99999/benchmark",
        "/api/v1/operations/failures/backtest_run/99999/rerun-plan",
    ):
        response = client.get(url)
        assert response.status_code == 404, response.text


def _factor_selection_payload(name: str) -> dict:
    return {
        "name": name,
        "factor_specs": [{"factor_name": "fixture_score", "factor_version": "v1"}],
        "top_n": 1,
        "start_date": "2026-01-05",
        "end_date": "2026-01-09",
        "initial_cash": "10000",
        "code_version": "api-split-regression",
        "data_version": "fixture-v1",
    }


def test_factor_selection_single_and_batch_persist_in_research_database(
    split_api, monkeypatch
):
    client, market, research = split_api

    class FixtureFactors:
        def list_for_cross_section(self, db, **kwargs):
            return [SimpleNamespace(
                trade_date=date(2026, 1, 5), symbol="600001.SH",
                value=Decimal("1"), run_id=1,
            )]

    monkeypatch.setattr(
        strategy_research_routes,
        "service",
        StrategyResearchService(
            factor_value_repository=FixtureFactors(),
            backtest_service=DailyBacktestService(research_read_source="control"),
        ),
    )
    root = "/api/v1/strategy-research/factor-selection/backtests"
    single_payload = _factor_selection_payload("single")
    single_payload.update(code_version="auto", data_version="auto")
    single = client.post(root, json=single_payload)
    assert single.status_code == 200, single.text
    single_id = single.json()["run_id"]
    single_run = client.get(f"/api/v1/backtests/runs/{single_id}")
    assert single_run.status_code == 200
    assert single_run.json()["code_version"].startswith("source-")
    assert single_run.json()["data_version"].startswith("input-")
    assert single_run.json()["parameters"]["data_version"] == (
        single_run.json()["data_version"]
    )

    batch = client.post(f"{root}/batch", json={
        "runs": [_factor_selection_payload("batch-a"), _factor_selection_payload("batch-b")],
    })
    assert batch.status_code == 200, batch.text
    assert [item["status"] for item in batch.json()] == ["success", "success"]
    run_ids = [single_id, *(item["result"]["run_id"] for item in batch.json())]
    for run_id in run_ids:
        assert client.get(f"/api/v1/backtests/runs/{run_id}").status_code == 200
        equity = client.get(f"/api/v1/backtests/runs/{run_id}/equity")
        assert equity.status_code == 200
        assert equity.json()["total"] == 5

    with Session(research) as db:
        assert db.scalar(text("SELECT count(*) FROM strategy.backtest_run")) == 3
        assert db.scalar(text("SELECT count(*) FROM strategy.backtest_equity")) == 15
    with Session(market) as db:
        assert db.scalar(text(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema='strategy' AND table_name='backtest_run'"
        )) == 0


def test_factor_selection_batch_rolls_back_failed_item_before_next_commit(
    split_api, monkeypatch
):
    client, _, research = split_api

    class FirstFails:
        calls = 0

        def run_factor_selection(self, db, **kwargs):
            self.calls += 1
            run = BacktestRepository().create_run(
                kwargs["research_db"],
                name=kwargs["name"], strategy_name=kwargs["strategy_name"],
                parameters={}, code_version=kwargs["code_version"],
                data_version=kwargs["data_version"],
                start_date=kwargs["start_date"], end_date=kwargs["end_date"],
                initial_cash=kwargs["initial_cash"],
            )
            if self.calls == 1:
                raise ValueError("fixture rejected after research flush")
            run.status = "success"
            return SimpleNamespace(
                run_id=run.id, quantile=None,
                result=SimpleNamespace(
                    initial_cash=kwargs["initial_cash"],
                    final_equity=kwargs["initial_cash"],
                    total_return=Decimal(), trades=[], equity_curve=[], positions=[],
                ),
            )

    monkeypatch.setattr(strategy_research_routes, "service", FirstFails())
    response = client.post(
        "/api/v1/strategy-research/factor-selection/backtests/batch",
        json={"runs": [
            _factor_selection_payload("failed-first"),
            _factor_selection_payload("successful-second"),
        ]},
    )
    assert response.status_code == 200, response.text
    assert [item["status"] for item in response.json()] == ["failed", "success"]
    with Session(research) as db:
        names = db.execute(text("SELECT name FROM strategy.backtest_run")).scalars().all()
        assert names == ["successful-second"]


def test_default_research_initialization_obeys_shared_lock(tmp_path, monkeypatch):
    engine = create_engine(f"duckdb:///{tmp_path / 'startup_artifacts.duckdb'}")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(research_session_module, "research_engine", engine)
    try:
        with duckdb_write_lock("data/research_write.lock"):
            with pytest.raises(DuckDbWriteLockTimeout):
                initialize_research_database()
        initialize_research_database()
        initialize_research_database()
        with Session(engine) as db:
            assert db.scalar(text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='strategy' AND table_name='backtest_run'"
            )) == 1
    finally:
        engine.dispose()
