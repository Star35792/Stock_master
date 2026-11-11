"""Production-shaped API regression: market and artifacts never share a database."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.api.routes.backtests import router as backtests_router
from app.api.routes.operations import router as operations_router
from app.api.routes.strategy_research import router as research_router
from app.database import research_session as research_session_module
from app.database.research_session import get_research_db, initialize_research_database
from app.database.session import get_db, initialize_database
from app.models.market import DailyBar, Security, TradingCalendar
from app.models.strategy import BacktestRun
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


def test_split_database_missing_report_is_404(split_api):
    client, _, _ = split_api
    for url in (
        "/api/v1/strategy-research/price-value/backtests/99999",
        "/api/v1/backtests/runs/99999/benchmark",
        "/api/v1/operations/failures/backtest_run/99999/rerun-plan",
    ):
        response = client.get(url)
        assert response.status_code == 404, response.text


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
