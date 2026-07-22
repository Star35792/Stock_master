import json
from decimal import Decimal
from hashlib import sha256
from typing import Any


def hash_payload(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def hash_paper_account_snapshot(account: Any, positions: list[Any]) -> str:
    return hash_payload(
        {
            "account_id": account.id,
            "cash_balance": str(Decimal(account.cash_balance).quantize(Decimal("0.0001"))),
            "updated_at": account.updated_at.isoformat(),
            "positions": [
                {
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "average_cost": str(
                        Decimal(position.average_cost).quantize(Decimal("0.000001"))
                    ),
                    "updated_at": position.updated_at.isoformat(),
                }
                for position in sorted(positions, key=lambda item: item.symbol)
            ],
        }
    )


def hash_paper_account_state(
    account_id: int,
    cash_balance: Decimal,
    positions: list[Any],
) -> str:
    def value(position: Any, name: str) -> Any:
        return position[name] if isinstance(position, dict) else getattr(position, name)

    return hash_payload(
        {
            "account_id": account_id,
            "cash_balance": str(Decimal(cash_balance).quantize(Decimal("0.0001"))),
            "positions": [
                {
                    "symbol": value(position, "symbol"),
                    "quantity": value(position, "quantity"),
                    "average_cost": str(
                        Decimal(value(position, "average_cost")).quantize(Decimal("0.000001"))
                    ),
                }
                for position in sorted(positions, key=lambda item: value(item, "symbol"))
                if value(position, "quantity") != 0
            ],
        }
    )


def hash_equity_market_snapshot(valuation_date: Any, components: list[Any]) -> str:
    def value(component: Any, name: str) -> Any:
        return component[name] if isinstance(component, dict) else getattr(component, name)

    return hash_payload(
        {
            "valuation_date": valuation_date.isoformat(),
            "bars": [
                {
                    "id": value(component, "daily_bar_id"),
                    "symbol": value(component, "symbol"),
                    "price": str(value(component, "price")),
                    "source_name": value(component, "source_name"),
                    "quality_status": value(component, "quality_status"),
                    "bar_updated_at": value(component, "bar_updated_at").isoformat(),
                }
                for component in sorted(
                    components,
                    key=lambda item: value(item, "symbol"),
                )
            ],
        }
    )
