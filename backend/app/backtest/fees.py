"""Decimal execution costs for explicit A-share research assumptions."""

from dataclasses import dataclass
from decimal import Decimal

A_SHARE_COST_V1 = "a_share_cost_v1"
ROUNDING_MODE = "ROUND_HALF_UP"


@dataclass(frozen=True)
class FeeResult:
    """Cost breakdown for a single fill."""

    fill_price: Decimal
    commission: Decimal
    stamp_duty: Decimal
    transfer_fee: Decimal
    exchange_fee: Decimal
    slippage_cost: Decimal

    @property
    def total_fee(self) -> Decimal:
        return (
            self.commission
            + self.stamp_duty
            + self.transfer_fee
            + self.exchange_fee
        )

    @property
    def total_cost(self) -> Decimal:
        """Total cash impact including explicit fees and slippage."""
        return self.total_fee + self.slippage_cost


@dataclass(frozen=True)
class AShareCostFeeModel:
    """A-share execution costs under explicit research assumptions.

    Components:
    - commission: bilateral, subject to ``min_commission``
    - stamp_duty: sell side only, using the supplied research rate
    - transfer_fee: bilateral, using the supplied rate without exchange inference
    - exchange fee: bilateral; callers must avoid double-counting bundled fees
    - slippage: buy price marked up, sell price marked down
    """

    version: str = A_SHARE_COST_V1
    name: str = "a_share_cost"
    commission_rate: Decimal = Decimal("0.0003")
    min_commission: Decimal = Decimal("5")
    stamp_duty_rate: Decimal = Decimal("0.0005")
    transfer_fee_rate: Decimal = Decimal("0.00001")
    exchange_fee_rate: Decimal = Decimal("0.0000487")
    slippage_bps: Decimal = Decimal("2")

    def apply(
        self,
        *,
        reference_price: Decimal,
        quantity: int,
        side: str,
    ) -> FeeResult:
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy or sell")
        slippage_rate = self.slippage_bps / Decimal(10000)
        fill_price = reference_price * (
            Decimal(1) + slippage_rate if side == "buy" else Decimal(1) - slippage_rate
        )
        amount = Decimal(quantity) * fill_price

        commission = (amount * self.commission_rate).quantize(
            Decimal("0.000001"), rounding=ROUNDING_MODE
        )
        commission = max(commission, self.min_commission)

        stamp_duty = (
            (amount * self.stamp_duty_rate).quantize(
                Decimal("0.000001"), rounding=ROUNDING_MODE
            )
            if side == "sell"
            else Decimal()
        )

        transfer_fee = (amount * self.transfer_fee_rate).quantize(
            Decimal("0.000001"), rounding=ROUNDING_MODE
        )
        exchange_fee = (amount * self.exchange_fee_rate).quantize(
            Decimal("0.000001"), rounding=ROUNDING_MODE
        )

        return FeeResult(
            fill_price=fill_price,
            commission=commission,
            stamp_duty=stamp_duty,
            transfer_fee=transfer_fee,
            exchange_fee=exchange_fee,
            slippage_cost=Decimal(quantity) * (fill_price - reference_price).copy_abs(),
        )

    def to_contract(self) -> dict[str, str]:
        return {
            "fee_model_version": self.version,
            "fee_model_name": self.name,
            "commission_rate": str(self.commission_rate),
            "min_commission": str(self.min_commission),
            "stamp_duty_rate": str(self.stamp_duty_rate),
            "transfer_fee_rate": str(self.transfer_fee_rate),
            "exchange_fee_rate": str(self.exchange_fee_rate),
            "slippage_bps": str(self.slippage_bps),
        }
