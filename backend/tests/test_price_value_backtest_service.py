from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.api.routes.strategy_research import router
from app.database.research_session import initialize_research_database
from app.database.session import get_db, initialize_database
from app.models.market import (
    CorporateAction,
    DailyBar,
    Security,
    SecurityStatusEvent,
    TradingCalendar,
)
from app.models.strategy import BacktestEquity, BacktestRun, BacktestTradeRecord
from app.models.trade import PaperAccount, PaperFill, PaperOrder, PaperPosition
from app.schemas.price_value_backtest import PriceValueBacktestRequest
from app.services.backtest import BacktestDataChangedError
from app.services.price_value_backtest import PriceValueBacktestService
from tests.test_price_value_research import research_payload


def backtest_payload(**changes):
    research = research_payload().model_dump(mode="json")
    values = {
        "name": "price-value historical assumptions",
        "symbol": research["symbol"],
        "start_date": "2026-01-05",
        "end_date": "2026-01-09",
        "initial_cash": "10000",
        "position_weight": "0.5",
        "required_margin": "0.3",
        "max_participation_rate": "0.01",
        "fees": research["fees"],
        "sell_stamp_duty_rate": "0.0005",
        "assumptions": [
            {
                "version": "v1",
                "valuation_at": "2026-01-04T16:00:00+08:00",
                "available_at": "2026-01-04T16:00:00+08:00",
                "valid_until": "2026-02-01T16:00:00+08:00",
                "source_reference": "User declared historical scenario, unverified",
                "diluted_shares": research["diluted_shares"],
                "scenarios": research["scenarios"],
            }
        ],
    }
    values.update(changes)
    return PriceValueBacktestRequest.model_validate(values)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'price-value.duckdb'}")
    initialize_database(engine)
    initialize_research_database(engine)
    with Session(engine) as db:
        db.add(
            Security(
                symbol="600001.SH",
                name="Fixture",
                security_type="stock",
                exchange="SH",
                currency="CNY",
                industry="食品饮料",
                is_active=True,
                list_date=date(2000, 1, 1),
            )
        )
        for offset, (open_, close) in enumerate(((6, 6), (6, 8), (8, 13), (13, 13), (13, 13))):
            day = date(2026, 1, 5 + offset)
            db.add(TradingCalendar(exchange="SH", trade_date=day, is_open=True))
            db.add(
                DailyBar(
                    symbol="600001.SH",
                    security_type="stock",
                    trade_date=day,
                    adjust_type="none",
                    open=Decimal(open_),
                    close=Decimal(close),
                    high=Decimal(max(open_, close)),
                    low=Decimal(min(open_, close)),
                    volume=Decimal("100000"),
                    amount=Decimal("1000000"),
                    limit_up=Decimal("100"),
                    limit_down=Decimal("1"),
                    is_suspended=False,
                    special_treatment_status="normal",
                    source_name="fixture",
                    quality_status="trusted",
                    is_suspect=False,
                )
            )
        db.commit()
    yield engine
    engine.dispose()


@pytest.fixture
def client(database):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def session():
        with Session(database) as db:
            yield db

    app.dependency_overrides[get_db] = session
    # Research artifacts share this test database (initialized in the
    # database fixture via initialize_research_database).
    from app.database.research_session import get_research_db

    app.dependency_overrides[get_research_db] = session
    with TestClient(app) as test_client:
        yield test_client


def test_api_persists_actual_cash_trades_and_retrievable_hypothetical_report(client, database):
    payload = backtest_payload().model_dump(mode="json")
    response = client.post("/api/v1/strategy-research/price-value/backtests", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["research_only"] and not result["pit_verified"] and not result["execution_eligible"]
    )
    assert result["trade_count"] == 2
    assert [trade["quantity"] for trade in result["trades"]] == [800, 800]
    assert result["trades"][0]["signal_date"] == "2026-01-05"
    assert result["trades"][0]["trade_date"] == "2026-01-06"
    assert Decimal(result["final_cash"]) == Decimal("15584.8")
    assert Decimal(result["final_equity"]) == Decimal("15584.8")
    assert Decimal(result["net_profit"]) == Decimal("5584.8")
    assert Decimal(result["total_return"]) == Decimal("0.55848")
    assert len(result["contract_hash"]) == 64
    read = client.get(f"/api/v1/strategy-research/price-value/backtests/{result['run_id']}")
    assert read.json() == result
    with Session(database) as db:
        run = db.get(BacktestRun, result["run_id"])
        assert run.status == "success"
        assert run.parameters["validation_mode"] == "none"
        assert run.parameters["data_snapshot"]["verification_scope"] == "independent_session"
        assert not run.parameters["data_snapshot"]["pit_verified"]
        assert run.parameters["engine_version"] == "price_value_single_asset_v1"
        assert run.parameters["calculation_precision_policy"].startswith("engine_decimal_80")
        assert run.parameters["market_snapshot"]["bars"][0]["source_name"] == "fixture"
        assert db.scalar(select(func.count()).select_from(BacktestTradeRecord)) == 2
        assert db.scalar(select(func.count()).select_from(BacktestEquity)) == 5
        for model in (PaperAccount, PaperOrder, PaperFill, PaperPosition):
            assert db.scalar(select(func.count()).select_from(model)) == 0
    assert client.get("/api/v1/strategy-research/price-value/backtests/99999").status_code == 404


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("limit_up", None, "price limits"),
        ("special_treatment_status", "ST", "treatment status"),
        ("special_treatment_status", None, "treatment status"),
        ("volume", None, "volume and amount"),
        ("volume", Decimal("0"), "unexplained trading state"),
        ("amount", Decimal("0"), "unexplained trading state"),
    ],
)
def test_incomplete_or_unsupported_market_data_never_creates_run(
    client, database, field, value, message
):
    with Session(database) as db:
        row = db.scalar(select(DailyBar).order_by(DailyBar.trade_date))
        setattr(row, field, value)
        db.commit()
    response = client.post(
        "/api/v1/strategy-research/price-value/backtests",
        json=backtest_payload().model_dump(mode="json"),
    )
    assert response.status_code == 422
    assert message in response.json()["detail"]
    with Session(database) as db:
        assert db.scalar(select(func.count()).select_from(BacktestRun)) == 0


def test_corporate_action_interval_is_rejected_before_persistence(client, database):
    with Session(database) as db:
        db.add(
            CorporateAction(
                symbol="600001.SH",
                ex_date=date(2026, 1, 7),
                action_type="cash_dividend",
                cash_dividend=Decimal("1"),
                source_name="fixture",
            )
        )
        db.commit()
    response = client.post(
        "/api/v1/strategy-research/price-value/backtests",
        json=backtest_payload().model_dump(mode="json"),
    )
    assert response.status_code == 422
    assert "corporate actions" in response.json()["detail"]


def test_independent_input_change_aborts_before_creating_run(database, monkeypatch):
    service = PriceValueBacktestService()
    original = service._load_inputs
    calls = 0

    def changing_snapshot(db, payload):
        nonlocal calls
        bars, days, snapshot = original(db, payload)
        calls += 1
        if calls == 2:
            snapshot["security"]["industry"] = "revised after calculation"
        return bars, days, snapshot

    monkeypatch.setattr(service, "_load_inputs", changing_snapshot)
    with Session(database) as db:
        with pytest.raises(BacktestDataChangedError):
            service.run(db, backtest_payload())
        assert db.scalar(select(func.count()).select_from(BacktestRun)) == 0


def test_current_unfinished_session_is_rejected(database):
    with Session(database) as db:
        with pytest.raises(ValueError, match="completed its trading session"):
            PriceValueBacktestService().run(
                db, backtest_payload(), now=datetime(2026, 1, 9, 7, 59, tzinfo=UTC)
            )


@pytest.fixture
def database_with_suspension(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'price-value-suspension.duckdb'}")
    initialize_database(engine)
    initialize_research_database(engine)
    with Session(engine) as db:
        db.add(
            Security(
                symbol="600001.SH",
                name="Fixture",
                security_type="stock",
                exchange="SH",
                currency="CNY",
                industry="食品饮料",
                is_active=True,
                list_date=date(2000, 1, 1),
            )
        )
        trading_days = [
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
            date(2026, 1, 8),
            date(2026, 1, 9),
        ]
        for day in trading_days:
            db.add(TradingCalendar(exchange="SH", trade_date=day, is_open=True))
        for offset, (open_, close) in enumerate(((6, 6), (6, 8), (8, 13), (13, 13))):
            # Skip 2026-01-07 to simulate a suspended trading day.
            day = trading_days[offset + (1 if offset >= 2 else 0)]
            db.add(
                DailyBar(
                    symbol="600001.SH",
                    security_type="stock",
                    trade_date=day,
                    adjust_type="none",
                    open=Decimal(open_),
                    close=Decimal(close),
                    high=Decimal(max(open_, close)),
                    low=Decimal(min(open_, close)),
                    volume=Decimal("100000"),
                    amount=Decimal("1000000"),
                    limit_up=Decimal("100"),
                    limit_down=Decimal("1"),
                    is_suspended=False,
                    special_treatment_status="normal",
                    source_name="fixture",
                    quality_status="trusted",
                    is_suspect=False,
                )
            )
        # Mark the missing day as suspended in the status event table.
        db.add(
            SecurityStatusEvent(
                symbol="600001.SH",
                trade_date=date(2026, 1, 7),
                status="suspended",
                source_name="fixture",
            )
        )
        db.commit()
    yield engine
    engine.dispose()


def test_suspended_day_without_bar_runs_and_records_wait_decision(
    database_with_suspension,
):
    service = PriceValueBacktestService()
    with Session(database_with_suspension) as db:
        response = service.run(db, backtest_payload())
    assert response.trade_count == 2
    decisions = response.decisions
    suspended = [item for item in decisions if item.reason == "suspended"]
    assert len(suspended) == 1
    assert suspended[0].trade_date == date(2026, 1, 7)
    # Position is held through the suspension at the last traded close.
    suspended_equity = [row for row in response.equity_curve if row.trade_date == date(2026, 1, 7)]
    assert suspended_equity[0].market_value == Decimal("6400")


@pytest.mark.parametrize(
    "changes",
    [
        {"position_weight": "NaN"},
        {"required_margin": "Infinity"},
        {"initial_cash": "NaN"},
        {"max_participation_rate": "NaN"},
        {"start_date": "2026-01-09"},
        {"start_date": "2020-01-01"},
    ],
)
def test_contract_rejects_nonfinite_and_unsupported_windows(changes):
    with pytest.raises(ValueError):
        backtest_payload(**changes)


def test_version_timeline_cannot_be_ambiguous_or_backdate_availability():
    assumption = backtest_payload().assumptions[0].model_dump(mode="json")
    with pytest.raises(ValueError, match="must be unique"):
        backtest_payload(assumptions=[assumption, assumption])
    with pytest.raises(ValueError, match="valuation_at <= available_at"):
        backtest_payload(assumptions=[{**assumption, "available_at": "2026-01-03T00:00:00Z"}])
