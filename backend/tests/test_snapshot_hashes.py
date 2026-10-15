from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from app.services.snapshot_hashes import (
    hash_equity_market_snapshot,
    hash_paper_account_snapshot,
    hash_paper_account_state,
    hash_payload,
)


def test_hash_payload_is_deterministic() -> None:
    payload = {"b": "1.2", "a": 1, "c": "x"}
    assert hash_payload(payload) == hash_payload(payload)


def test_hash_payload_orders_keys_and_serializes_decimals() -> None:
    # JSON serialization will turn Decimal into a string; sort_keys ensures order.
    assert hash_payload({"z": 1, "a": 2}) == hash_payload({"a": 2, "z": 1})


def test_hash_payload_is_sensitive_to_values() -> None:
    assert hash_payload({"a": 1}) != hash_payload({"a": 2})


def test_hash_paper_account_snapshot_sorts_positions() -> None:
    updated_at = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
    account = SimpleNamespace(
        id=1,
        cash_balance=Decimal("12345.6789"),
        updated_at=updated_at,
    )
    positions = [
        SimpleNamespace(
            symbol="BBB",
            quantity=200,
            average_cost=Decimal("10.5"),
            updated_at=updated_at,
        ),
        SimpleNamespace(
            symbol="AAA",
            quantity=100,
            average_cost=Decimal("5.25"),
            updated_at=updated_at,
        ),
    ]

    hash_a = hash_paper_account_snapshot(account, positions)
    hash_b = hash_paper_account_snapshot(account, list(reversed(positions)))
    assert hash_a == hash_b


def test_hash_paper_account_snapshot_ignores_beyond_quantization_precision() -> None:
    updated_at = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
    account = SimpleNamespace(
        id=1,
        cash_balance=Decimal("12345.67890001"),
        updated_at=updated_at,
    )
    positions = [
        SimpleNamespace(
            symbol="AAA",
            quantity=100,
            average_cost=Decimal("5.2500000001"),
            updated_at=updated_at,
        ),
    ]
    hash_a = hash_paper_account_snapshot(account, positions)

    account2 = SimpleNamespace(
        id=1,
        cash_balance=Decimal("12345.67890002"),
        updated_at=updated_at,
    )
    positions2 = [
        SimpleNamespace(
            symbol="AAA",
            quantity=100,
            average_cost=Decimal("5.2500000002"),
            updated_at=updated_at,
        ),
    ]
    hash_b = hash_paper_account_snapshot(account2, positions2)
    assert hash_a == hash_b


def test_hash_paper_account_state_ignores_zero_quantity_positions() -> None:
    result = hash_paper_account_state(
        account_id=1,
        cash_balance=Decimal("100"),
        positions=[
            {"symbol": "AAA", "quantity": 0, "average_cost": Decimal("10")},
            {"symbol": "BBB", "quantity": 50, "average_cost": Decimal("20")},
        ],
    )
    result_without_zero = hash_paper_account_state(
        account_id=1,
        cash_balance=Decimal("100"),
        positions=[
            {"symbol": "BBB", "quantity": 50, "average_cost": Decimal("20")},
        ],
    )
    assert result == result_without_zero


def test_hash_paper_account_state_sorts_positions() -> None:
    positions = [
        {"symbol": "ZZZ", "quantity": 1, "average_cost": Decimal("1")},
        {"symbol": "AAA", "quantity": 2, "average_cost": Decimal("2")},
    ]
    hash_a = hash_paper_account_state(1, Decimal("100"), positions)
    hash_b = hash_paper_account_state(1, Decimal("100"), list(reversed(positions)))
    assert hash_a == hash_b


def test_hash_equity_market_snapshot_sorts_components() -> None:
    valuation_date = datetime(2026, 7, 21, tzinfo=UTC).date()
    updated_at = datetime(2026, 7, 21, 15, 0, 0, tzinfo=UTC)
    components = [
        SimpleNamespace(
            daily_bar_id=2,
            symbol="BBB",
            price=Decimal("20"),
            source_name="hithink",
            quality_status="trusted",
            bar_updated_at=updated_at,
        ),
        SimpleNamespace(
            daily_bar_id=1,
            symbol="AAA",
            price=Decimal("10"),
            source_name="hithink",
            quality_status="trusted",
            bar_updated_at=updated_at,
        ),
    ]

    hash_a = hash_equity_market_snapshot(valuation_date, components)
    hash_b = hash_equity_market_snapshot(valuation_date, list(reversed(components)))
    assert hash_a == hash_b
