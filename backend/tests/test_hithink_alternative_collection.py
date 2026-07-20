from datetime import date
from decimal import Decimal

from app.data_sources import HithinkFinanceDataSource
from app.services.hithink_alternative_collection import (
    _flatten_ladder,
    _normalize_payload,
)


def test_limit_up_pool_auto_paginates() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    pages = []

    def fake_get(path: str, params: dict) -> dict:
        pages.append(params["page"])
        return {
            "timestamp": 1,
            "pagination": {"pages": 2},
            "item": [{"thscode": f"00000{params['page']}.SZ"}],
        }

    source._get = fake_get  # type: ignore[method-assign]
    payload = source.fetch_limit_up_pool()

    assert pages == [1, 2]
    assert len(payload["item"]) == 2


def test_alternative_payload_normalizes_common_fields() -> None:
    rows = _normalize_payload(
        "hot_stock",
        date(2026, 7, 17),
        {
            "item": [
                {
                    "thscode": "600519.SH",
                    "rank": 1,
                    "heat": "12345",
                    "name": "贵州茅台",
                }
            ]
        },
        "hithink_finance",
    )

    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["rank"] == 1
    assert rows[0]["value"] == Decimal("12345")


def test_limit_up_ladder_flattens_board_matrix() -> None:
    rows = _flatten_ladder(
        [
            {
                "date": "20260717",
                "boards": {
                    "two_board": [{"thscode": "000001.SZ"}],
                    "three_board": [],
                },
            }
        ]
    )

    assert rows == [
        {"thscode": "000001.SZ", "date": "20260717", "board": "two_board"}
    ]
