from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _fake_market(monkeypatch, *, symbols: set[str], prices: dict[str, str]) -> None:
    route_service = __import__(
        "app.api.routes.position_books", fromlist=["service"]
    ).service
    known = set(symbols)

    monkeypatch.setattr(
        route_service.security_repository,
        "get_many",
        lambda db, symbols=None, **kw: [
            SimpleNamespace(
                symbol=symbol,
                name=f"证券{symbol}",
                industry="银行",
                security_type="stock",
                exchange="SH",
            )
            for symbol in sorted((symbols or set()) & known)
        ],
    )
    price_map = {symbol: Decimal(price) for symbol, price in prices.items()}
    monkeypatch.setattr(
        route_service.daily_bar_repository,
        "list_latest_consumable_by_symbols",
        lambda db, *, symbols, adjust_type: [
            SimpleNamespace(
                symbol=symbol,
                trade_date=date(2026, 7, 21),
                adjust_type=adjust_type,
                close=price_map[symbol],
                quality_status="trusted",
            )
            for symbol in sorted(set(symbols) & set(price_map))
        ],
    )


def test_position_book_crud_and_holdings_flow(monkeypatch) -> None:
    created = client.post(
        "/api/v1/position-books",
        json={"name": "主账户", "cash": "1000", "note": None},
    )
    assert created.status_code == 201
    book = created.json()
    assert book["cash"] == "1000.0000"

    duplicate = client.post("/api/v1/position-books", json={"name": "主账户"})
    assert duplicate.status_code == 409

    _fake_market(monkeypatch, symbols={"600000.SH"}, prices={"600000.SH": "12.5"})
    trade = client.post(
        f"/api/v1/position-books/{book['id']}/trades",
        headers={"Idempotency-Key": "trade-api-1"},
        json={
            "side": "buy",
            "symbol": "600000.SH",
            "quantity": 100,
            "price": "10",
            "fee": "5",
            "trade_date": "2026-07-20",
        },
    )
    assert trade.status_code == 201
    assert trade.json()["price"] == "10.0000"

    holdings = client.get(f"/api/v1/position-books/{book['id']}/holdings")
    assert holdings.status_code == 200
    payload = holdings.json()
    holding = payload["holdings"][0]
    assert holding["symbol"] == "600000.SH"
    assert holding["market_value"] == "1250.0000"
    # Cost basis includes the buy fee: 100 * 10 + 5 = 1005.
    assert holding["unrealized_pnl"] == "245.0000"
    assert payload["total_equity"] == "2250.0000"
    assert payload["industry_exposure"][0]["industry"] == "银行"

    trades = client.get(f"/api/v1/position-books/{book['id']}/trades")
    assert trades.status_code == 200
    assert len(trades.json()) == 1


def test_position_trade_unknown_symbol_is_422(monkeypatch) -> None:
    created = client.post("/api/v1/position-books", json={"name": "空账户"})
    book_id = created.json()["id"]

    # In-memory DuckDB pools are thread-bound, so query DuckDB via a patched
    # empty master-data result instead of real SQL from the request thread.
    route_service = __import__(
        "app.api.routes.position_books", fromlist=["service"]
    ).service
    monkeypatch.setattr(
        route_service.security_repository,
        "get_many",
        lambda db, symbols=None, **kw: [],
    )

    response = client.post(
        f"/api/v1/position-books/{book_id}/trades",
        headers={"Idempotency-Key": "trade-api-unknown"},
        json={
            "side": "buy",
            "symbol": "999999.SH",
            "quantity": 100,
            "price": "10",
            "trade_date": "2026-07-20",
        },
    )
    assert response.status_code == 422
    assert "master data" in response.json()["detail"]


def test_position_book_holdings_unknown_book_is_404() -> None:
    response = client.get("/api/v1/position-books/999/holdings")
    assert response.status_code == 404


def test_plan_drift_requires_exactly_one_source() -> None:
    response = client.get("/api/v1/portfolio/plans/1/drift")
    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]
