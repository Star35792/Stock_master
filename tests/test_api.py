from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_contains_v1_routes() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/api/v1/securities" in paths
    assert "/api/v1/daily-bars/{symbol}" in paths
    assert "/api/v1/data-quality/issues" in paths


def test_daily_bar_rejects_reversed_date_range() -> None:
    response = client.get(
        "/api/v1/daily-bars/600519.SH",
        params={"start_date": "2026-07-16", "end_date": "2026-07-15"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "start_date must be earlier than or equal to end_date"
