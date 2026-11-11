from datetime import date
from decimal import Decimal

from app.data_sources import HithinkFinanceDataSource
from app.services.hithink_alternative_collection import (
    _flatten_hot_money,
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


def test_hot_money_board_is_grouped_by_seat_not_flat() -> None:
    """端点的游资榜是「席位 → 个股」两层结构，不拍平就是 0 行。"""
    seats = [
        {
            "name": "温州帮",
            "buying": 46681951.63,
            "rows": [
                {"thscode": "002048.SZ", "hot_money_item_net_value": 45942275.0},
                {"thscode": "002086.SZ", "hot_money_item_net_value": 100.0},
            ],
        },
        {"name": "赵老哥", "buying": 100.0, "rows": [{"thscode": "002048.SZ"}]},
    ]

    rows = _flatten_hot_money(seats)

    assert len(rows) == 3
    assert {row["hot_money_seat"] for row in rows} == {"温州帮", "赵老哥"}
    assert rows[0]["hot_money_seat_buying"] == 46681951.63
    assert rows[2]["hot_money_seat"] == "赵老哥"


def test_hot_money_rows_keep_seat_identity_in_the_item_key() -> None:
    """同一只股票出现在两个席位下时，唯一键不能把其中一个覆盖掉。"""
    payload = {
        "item": [
            {
                "thscode": "600609.SH",
                "hot_money_seat": "温州帮",
                "hot_money_item_net_value": 25979099.0,
                "hot_rank": 3,
            },
            {
                "thscode": "600609.SH",
                "hot_money_seat": "赵老哥",
                "hot_money_item_net_value": -1000.0,
                "hot_rank": 5,
            },
        ]
    }

    rows = _normalize_payload(
        "dragon_tiger_hot_money", date(2026, 11, 10), payload, "hithink_finance"
    )

    assert [row["item_key"] for row in rows] == ["温州帮|600609.SH", "赵老哥|600609.SH"]
    assert all(row["symbol"] == "600609.SH" for row in rows)
    # 席位口径的净买额，不是总榜口径
    assert rows[0]["value"] == Decimal("25979099.0")
    assert rows[1]["value"] == Decimal("-1000.0")
