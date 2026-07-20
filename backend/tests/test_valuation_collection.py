from datetime import date
from decimal import Decimal

from app.services.valuation_collection import (
    HithinkValuationCollectionService,
    _normalize_snapshot,
)


class FakeDataSource:
    name = "hithink_finance"

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.kwargs: dict | None = None

    def fetch_price_snapshots(self, **kwargs: object) -> list[dict]:
        self.kwargs = kwargs
        return self.items


class FakeValuationSnapshotRepository:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, db: object, *, rows: list[dict]) -> int:
        self.rows.extend(rows)
        return len(rows)


def test_normalize_snapshot_maps_market_valuation_fields() -> None:
    row = _normalize_snapshot(
        {
            "thscode": "600519.SH",
            "date_ms": 1784217600000,
            "last_price": "100",
            "total_share": "20",
            "float_share": "10",
            "pe_ttm": "15",
            "pb": "3",
            "ps_ttm": "8",
            "currency": "CNY",
        },
        "hithink_finance",
    )

    assert row is not None
    assert row["symbol"] == "600519.SH"
    assert row["trade_date"] == date(2026, 7, 17)
    assert row["total_market_cap"] == Decimal("2000")
    assert row["float_market_cap"] == Decimal("1000")
    assert row["pe_ttm"] == Decimal("15")
    assert row["pb"] == Decimal("3")
    assert row["ps_ttm"] == Decimal("8")


def test_valuation_collection_service_fetches_and_upserts_snapshots() -> None:
    data_source = FakeDataSource(
        [
            {
                "thscode": "600519.SH",
                "trade_date": "2026-07-17",
                "market_cap": "2000",
                "float_market_cap": "1500",
            }
        ]
    )
    repository = FakeValuationSnapshotRepository()
    service = HithinkValuationCollectionService(
        data_source=data_source,  # type: ignore[arg-type]
        repository=repository,  # type: ignore[arg-type]
    )

    result = service.collect_current(
        object(),
        symbols=["600519.sh"],
        fetch_all_market=False,
        limit=100,
        offset=0,
    )

    assert result.input_rows == 1
    assert result.normalized_rows == 1
    assert data_source.kwargs == {
        "symbols": ["600519.SH"],
        "fetch_all_market": False,
        "limit": 100,
        "offset": 0,
    }
    assert repository.rows[0]["total_market_cap"] == Decimal("2000")
