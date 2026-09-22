"""Exercise the real API/service/repositories against isolated DuckDB market tables."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.strategy_research import router
from app.database.base import Base
from app.database.session import get_db
from app.models.market import DailyBar, Security
from tests.test_price_value_research import research_payload


@pytest.fixture
def isolated_api():
    engine = create_engine("duckdb:///:memory:", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA market"))
    Base.metadata.create_all(engine, tables=[Security.__table__, DailyBar.__table__])
    db = Session(engine, expire_on_commit=False)
    quote_time = datetime(2026, 1, 5, 9, tzinfo=UTC)
    db.add(
        Security(
            symbol="600001.SH",
            name="TEST COMPANY",
            security_type="stock",
            exchange="SH",
            currency="CNY",
            industry="食品饮料",
            list_date=date(2000, 1, 1),
            is_active=True,
        )
    )
    db.add(
        DailyBar(
            symbol="600001.SH",
            security_type="stock",
            trade_date=date(2026, 1, 5),
            adjust_type="none",
            open=Decimal("6.5"),
            high=Decimal("6.8"),
            low=Decimal("6.4"),
            close=Decimal("6.5"),
            volume=Decimal("1000000"),
            amount=Decimal("6500000"),
            source_name="isolated-test-source",
            quality_status="trusted",
            is_suspect=False,
            special_treatment_status="normal",
            is_suspended=False,
            created_at=quote_time,
            updated_at=quote_time,
        )
    )
    db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, db
    db.close()
    engine.dispose()


URL = "/api/v1/strategy-research/price-value/evaluate"


def test_api_uses_real_market_tables_and_never_writes(isolated_api):
    client, db = isolated_api
    before = db.scalar(select(func.count()).select_from(DailyBar))
    before_updated = db.scalar(select(DailyBar.updated_at))
    response = client.post(URL, json=research_payload().model_dump(mode="json"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "price_condition_met"
    assert Decimal(body["valuation"]["low"]) == Decimal("10")
    assert body["market_snapshot"]["source_name"] == "isolated-test-source"
    assert body["market_snapshot"]["security_type"] == "stock"
    assert not body["execution_eligible"] and not body["pit_verified"]
    assert body["research_only"] and body["new_allocation"] == "none"
    assert db.scalar(select(func.count()).select_from(DailyBar)) == before
    assert db.scalar(select(DailyBar.updated_at)) == before_updated
    assert not db.new and not db.dirty and not db.deleted
    assert client.get("/openapi.json").json()["paths"][URL]["post"]["responses"]["200"]


@pytest.mark.parametrize("field", ["research_only", "pit_verified", "execution_eligible", "price"])
def test_api_rejects_client_override_of_trust_or_market_price(isolated_api, field):
    client, _ = isolated_api
    data = research_payload().model_dump(mode="json")
    data[field] = True
    response = client.post(URL, json=data)
    assert response.status_code == 422


def test_missing_quote_returns_explained_block_instead_of_demo(isolated_api):
    client, _ = isolated_api
    data = research_payload().model_dump(mode="json")
    data["trade_date"] = "2026-01-04"
    response = client.post(URL, json=data)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked" and body["price_metrics"] is None
    assert "quote_missing" in {reason["code"] for reason in body["blocking_reasons"]}


def test_future_time_returns_block_from_server(isolated_api):
    client, _ = isolated_api
    data = research_payload().model_dump(mode="json")
    data["valuation_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    response = client.post(URL, json=data)
    assert response.status_code == 200
    assert "future_valuation" in {reason["code"] for reason in response.json()["blocking_reasons"]}


def test_scenario_order_error_maps_to_422(isolated_api):
    client, _ = isolated_api
    data = research_payload().model_dump(mode="json")
    data["scenarios"]["low"], data["scenarios"]["high"] = (
        data["scenarios"]["high"],
        data["scenarios"]["low"],
    )
    response = client.post(URL, json=data)
    assert response.status_code == 422
    assert "low <= base <= high" in response.json()["detail"]


def test_invalid_number_returns_validation_error(isolated_api):
    client, _ = isolated_api
    data = research_payload().model_dump(mode="json")
    data["scenarios"]["low"]["cash_flows"] = ["NaN"]
    assert client.post(URL, json=data).status_code == 422
