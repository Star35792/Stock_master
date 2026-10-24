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


def test_position_book_patch_updates_and_maps_errors(monkeypatch) -> None:
    created = client.post("/api/v1/position-books", json={"name": "改前", "cash": "500"})
    book_id = created.json()["id"]

    patched = client.patch(
        f"/api/v1/position-books/{book_id}",
        json={"cash": "1500", "note": "调整现金"},
    )
    assert patched.status_code == 200
    assert patched.json()["cash"] == "1500.0000"
    assert patched.json()["note"] == "调整现金"

    other = client.post("/api/v1/position-books", json={"name": "占用名"})
    conflict = client.patch(
        f"/api/v1/position-books/{book_id}",
        json={"name": "占用名"},
    )
    assert conflict.status_code == 409
    assert other.status_code == 201

    missing = client.patch("/api/v1/position-books/999", json={"cash": "1"})
    assert missing.status_code == 404


def test_position_trade_delete_flow(monkeypatch) -> None:
    created = client.post("/api/v1/position-books", json={"name": "删流水账本"})
    book_id = created.json()["id"]
    _fake_market(monkeypatch, symbols={"600000.SH"}, prices={"600000.SH": "12.5"})
    trade = client.post(
        f"/api/v1/position-books/{book_id}/trades",
        headers={"Idempotency-Key": "trade-del-1"},
        json={
            "side": "buy",
            "symbol": "600000.SH",
            "quantity": 100,
            "price": "10",
            "trade_date": "2026-07-20",
        },
    )
    trade_id = trade.json()["id"]

    deleted = client.delete(f"/api/v1/position-books/{book_id}/trades/{trade_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/position-books/{book_id}/trades").json() == []

    missing = client.delete(f"/api/v1/position-books/{book_id}/trades/{trade_id}")
    assert missing.status_code == 404


def test_position_trade_delete_rejected_when_it_breaks_net_position(monkeypatch) -> None:
    created = client.post("/api/v1/position-books", json={"name": "负持仓账本"})
    book_id = created.json()["id"]
    _fake_market(monkeypatch, symbols={"600000.SH"}, prices={"600000.SH": "12.5"})
    buy = client.post(
        f"/api/v1/position-books/{book_id}/trades",
        headers={"Idempotency-Key": "trade-del-buy"},
        json={
            "side": "buy",
            "symbol": "600000.SH",
            "quantity": 100,
            "price": "10",
            "trade_date": "2026-07-20",
        },
    )
    client.post(
        f"/api/v1/position-books/{book_id}/trades",
        headers={"Idempotency-Key": "trade-del-sell"},
        json={
            "side": "sell",
            "symbol": "600000.SH",
            "quantity": 100,
            "price": "11",
            "trade_date": "2026-07-21",
        },
    )

    # Deleting the covering buy would leave net -100 for the symbol.
    response = client.delete(f"/api/v1/position-books/{book_id}/trades/{buy.json()['id']}")
    assert response.status_code == 422
    assert "negative net position" in response.json()["detail"]
    # The rejected deletion was rolled back: both trades still exist.
    assert len(client.get(f"/api/v1/position-books/{book_id}/trades").json()) == 2


def test_plan_drift_happy_path_over_http(monkeypatch) -> None:
    created = client.post("/api/v1/position-books", json={"name": "偏离账本", "cash": "1000"})
    book_id = created.json()["id"]

    plan = SimpleNamespace(
        id=3,
        name="draft",
        total_equity=Decimal("2500"),
        target_cash=Decimal("250"),
    )
    targets = [
        SimpleNamespace(
            symbol="600000.SH",
            security_type="stock",
            industry="银行",
            final_weight=Decimal("0.4"),
        )
    ]

    def fake_get_with_targets(db, *, plan_id: int):
        return (plan, targets) if plan_id == 3 else (None, [])

    drift_module = __import__("app.api.routes.portfolio", fromlist=["drift_service"])
    monkeypatch.setattr(
        drift_module.drift_service,
        "plan_repository",
        SimpleNamespace(get_with_targets=fake_get_with_targets),
    )
    valuation = SimpleNamespace(
        symbol="600000.SH",
        quantity=150,
        cost_basis=Decimal("1000"),
        average_cost=Decimal("10"),
        price=Decimal("10"),
        price_as_of=date(2026, 7, 21),
        price_quality_status="trusted",
        price_stale=False,
        market_value=Decimal("1500"),
        unrealized_pnl=Decimal("500"),
        unrealized_pnl_pct=Decimal("0.5"),
        realized_pnl=Decimal("0"),
        extras={},
    )
    monkeypatch.setattr(
        drift_module.drift_service.position_book_service,
        "get_valuation",
        lambda positions_db, market_db, *, book_id: (
            SimpleNamespace(id=book_id, name="偏离账本", cash_quants=10_000_000),
            Decimal("1000"),
            {"600000.SH": valuation},
            date(2026, 7, 21),
        ),
    )

    response = client.get(f"/api/v1/portfolio/plans/3/drift?position_book_id={book_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "position_book"
    assert payload["source_name"] == "偏离账本"
    row = payload["rows"][0]
    assert row["direction"] == "sell"
    assert row["drift"] == "0.2000000000"
    assert payload["summary"]["off_threshold_count"] == 1


def test_plan_drift_requires_exactly_one_source() -> None:
    response = client.get("/api/v1/portfolio/plans/1/drift")
    assert response.status_code == 422
    assert "exactly one" in response.json()["detail"]
