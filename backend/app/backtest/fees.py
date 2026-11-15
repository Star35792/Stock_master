"""Backtest fee models.

The fee model is versioned so research contracts retain historical cost
assumptions. V1 remains stable; V2 uses instrument type and trade date.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

LINEAR_COMMISSION_V1 = "linear_commission_v1"
A_SHARE_COST_V1 = "a_share_cost_v1"
A_SHARE_COST_V2 = "a_share_cost_v2"

_MODERN_TRANSFER_FEE_START = date(2022, 4, 29)
_STAMP_DUTY_HALVING_DATE = date(2023, 8, 28)

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


class FeeModel(Protocol):
    """Protocol for backtest cost models."""

    version: str
    name: str

    def apply(
        self,
        *,
        reference_price: Decimal,
        quantity: int,
        side: str,
        symbol: str | None = None,
        security_type: str | None = None,
        trade_date: date | None = None,
    ) -> FeeResult: ...


@dataclass(frozen=True)
class LinearCommissionFeeModel:
    """Legacy model: commission + slippage only, no minimum commission."""

    version: str = LINEAR_COMMISSION_V1
    name: str = "linear_commission"
    commission_rate: Decimal = Decimal("0.0003")
    slippage_bps: Decimal = Decimal("2")

    def apply(
        self,
        *,
        reference_price: Decimal,
        quantity: int,
        side: str,
        symbol: str | None = None,
        security_type: str | None = None,
        trade_date: date | None = None,
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
        return FeeResult(
            fill_price=fill_price,
            commission=commission,
            stamp_duty=Decimal(),
            transfer_fee=Decimal(),
            exchange_fee=Decimal(),
            slippage_cost=Decimal(quantity) * (fill_price - reference_price).copy_abs(),
        )


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
        symbol: str | None = None,
        security_type: str | None = None,
        trade_date: date | None = None,
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


@dataclass(frozen=True)
class AShareCostV2FeeModel(AShareCostFeeModel):
    """Instrument-aware costs for trades from 2022-04-29 onward.

    V1 remains frozen.  V2 requires explicit instrument type and fill date so
    ETF trades never inherit stock taxes and the 2023 stamp-duty change is
    applied to historical stock sells.  Earlier transfer-fee schedules are
    outside this version and fail closed.
    """

    version: str = A_SHARE_COST_V2
    name: str = "a_share_cost_instrument_aware"
    legal_stamp_duty_schedule: bool = True

    def apply(
        self,
        *,
        reference_price: Decimal,
        quantity: int,
        side: str,
        symbol: str | None = None,
        security_type: str | None = None,
        trade_date: date | None = None,
    ) -> FeeResult:
        if not symbol or security_type not in {"stock", "etf"} or trade_date is None:
            raise ValueError("a_share_cost_v2 requires symbol, security_type and trade_date")
        if trade_date < _MODERN_TRANSFER_FEE_START:
            raise ValueError("a_share_cost_v2 requires trade_date >= 2022-04-29")
        result = super().apply(
            reference_price=reference_price,
            quantity=quantity,
            side=side,
        )
        if security_type == "etf":
            return FeeResult(
                fill_price=result.fill_price,
                commission=result.commission,
                stamp_duty=Decimal(),
                transfer_fee=Decimal(),
                exchange_fee=result.exchange_fee,
                slippage_cost=result.slippage_cost,
            )
        if self.legal_stamp_duty_schedule and side == "sell":
            rate = (
                Decimal("0.001")
                if trade_date < _STAMP_DUTY_HALVING_DATE
                else Decimal("0.0005")
            )
            amount = Decimal(quantity) * result.fill_price
            stamp_duty = (amount * rate).quantize(
                Decimal("0.000001"), rounding=ROUNDING_MODE
            )
            return FeeResult(
                fill_price=result.fill_price,
                commission=result.commission,
                stamp_duty=stamp_duty,
                transfer_fee=result.transfer_fee,
                exchange_fee=result.exchange_fee,
                slippage_cost=result.slippage_cost,
            )
        return result

    def to_contract(self) -> dict[str, str]:
        return {
            **super().to_contract(),
            "security_type_fee_rule": "stock_or_etf_v1",
            "stock_stamp_duty_rule": (
                "legal_20230828_v1" if self.legal_stamp_duty_schedule else "fixed_rate_v1"
            ),
            "supported_trade_date_start": _MODERN_TRANSFER_FEE_START.isoformat(),
        }


def build_fee_model(
    *,
    fee_model_version: str = LINEAR_COMMISSION_V1,
    commission_rate: Decimal = Decimal("0.0003"),
    slippage_bps: Decimal = Decimal("2"),
    min_commission: Decimal | None = None,
    sell_stamp_duty_rate: Decimal | None = None,
    transfer_fee_rate: Decimal | None = None,
    exchange_fee_rate: Decimal | None = None,
) -> FeeModel:
    """Factory for versioned fee models.

    Omitted components use the selected model's defaults. Explicit zero values
    are preserved so execution matches the recorded research cost assumptions.
    """
    if fee_model_version in {A_SHARE_COST_V1, A_SHARE_COST_V2}:
        model_type = (
            AShareCostFeeModel
            if fee_model_version == A_SHARE_COST_V1
            else AShareCostV2FeeModel
        )
        model_kwargs = dict(
            commission_rate=commission_rate,
            min_commission=Decimal("5") if min_commission is None else min_commission,
            stamp_duty_rate=(
                Decimal("0.0005") if sell_stamp_duty_rate is None else sell_stamp_duty_rate
            ),
            transfer_fee_rate=(
                Decimal("0.00001") if transfer_fee_rate is None else transfer_fee_rate
            ),
            exchange_fee_rate=(
                Decimal("0.0000487") if exchange_fee_rate is None else exchange_fee_rate
            ),
            slippage_bps=slippage_bps,
        )
        if fee_model_version == A_SHARE_COST_V2:
            model_kwargs["legal_stamp_duty_schedule"] = sell_stamp_duty_rate is None
        return model_type(**model_kwargs)
    if fee_model_version == LINEAR_COMMISSION_V1:
        if min_commission or sell_stamp_duty_rate or transfer_fee_rate or exchange_fee_rate:
            raise ValueError(
                "linear_commission_v1 does not support min_commission, stamp_duty, "
                "transfer_fee or exchange_fee"
            )
        return LinearCommissionFeeModel(
            commission_rate=commission_rate,
            slippage_bps=slippage_bps,
        )
    raise ValueError(f"unsupported fee_model_version: {fee_model_version}")
