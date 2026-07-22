from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol


@dataclass(frozen=True)
class BrokerOrderRequest:
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    trade_date: date


@dataclass(frozen=True)
class BrokerFill:
    symbol: str
    side: str
    quantity: int
    price: Decimal
    commission: Decimal
    trade_date: date


class BrokerAdapter(Protocol):
    def execute(self, order: BrokerOrderRequest) -> BrokerFill: ...


class PaperBrokerAdapter:
    def __init__(
        self,
        *,
        commission_rate: Decimal = Decimal("0.0003"),
        slippage_bps: Decimal = Decimal("2"),
    ) -> None:
        if commission_rate < 0:
            raise ValueError("commission_rate must be non-negative")
        if slippage_bps < 0:
            raise ValueError("slippage_bps must be non-negative")
        self.commission_rate = commission_rate
        self.slippage_bps = slippage_bps

    def execute(self, order: BrokerOrderRequest) -> BrokerFill:
        if order.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if order.quantity <= 0:
            raise ValueError("quantity must be positive")
        if order.reference_price <= 0:
            raise ValueError("reference_price must be positive")
        slippage_rate = self.slippage_bps / Decimal(10000)
        price = order.reference_price * (
            Decimal(1) + slippage_rate if order.side == "buy" else Decimal(1) - slippage_rate
        )
        price = price.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        commission = (Decimal(order.quantity) * price * self.commission_rate).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        return BrokerFill(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=price,
            commission=commission,
            trade_date=order.trade_date,
        )
