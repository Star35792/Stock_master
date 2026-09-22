from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from app.backtest.fees import (
    LINEAR_COMMISSION_V1,
    FeeModel,
    build_fee_model,
)
from app.backtest.tradability import AShareTradabilityPolicy


@dataclass(frozen=True)
class BrokerOrderRequest:
    symbol: str
    side: str
    quantity: int
    reference_price: Decimal
    trade_date: date
    # Optional execution bar for A-share tradability checks.
    bar: Any | None = None


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
        fee_model: FeeModel | None = None,
        tradability_policy: AShareTradabilityPolicy | None = None,
    ) -> None:
        if fee_model is not None:
            self.fee_model = fee_model
        else:
            if commission_rate < 0:
                raise ValueError("commission_rate must be non-negative")
            if slippage_bps < 0:
                raise ValueError("slippage_bps must be non-negative")
            self.fee_model = build_fee_model(
                fee_model_version=LINEAR_COMMISSION_V1,
                commission_rate=commission_rate,
                slippage_bps=slippage_bps,
            )
        self.tradability_policy = tradability_policy or AShareTradabilityPolicy()

    def execute(self, order: BrokerOrderRequest) -> BrokerFill:
        if order.side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        if order.quantity <= 0:
            raise ValueError("quantity must be positive")
        if order.reference_price <= 0:
            raise ValueError("reference_price must be positive")
        if order.bar is not None:
            tradability = self.tradability_policy.check(order.bar)
            if order.side == "buy" and not tradability.can_buy:
                raise _PaperExecutionBlocked(
                    code="buy_blocked_limit_up_or_suspended",
                    message=tradability.reason or "buy is not tradable",
                )
            if order.side == "sell" and not tradability.can_sell:
                raise _PaperExecutionBlocked(
                    code="sell_blocked_limit_down_or_suspended",
                    message=tradability.reason or "sell is not tradable",
                )
        fee_result = self.fee_model.apply(
            reference_price=order.reference_price,
            quantity=order.quantity,
            side=order.side,
        )
        price = fee_result.fill_price.quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP
        )
        commission = fee_result.total_fee.quantize(
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


class _PaperExecutionBlocked(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
