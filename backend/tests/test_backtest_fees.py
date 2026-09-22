from decimal import Decimal

import pytest

from app.backtest import (
    AShareCostFeeModel,
    LinearCommissionFeeModel,
    build_fee_model,
)
from app.backtest.fees import A_SHARE_COST_V1, LINEAR_COMMISSION_V1


def test_linear_commission_model_matches_legacy_behavior() -> None:
    model = LinearCommissionFeeModel(
        commission_rate=Decimal("0.001"),
        slippage_bps=Decimal("10"),
    )
    result = model.apply(
        reference_price=Decimal("10"),
        quantity=100,
        side="buy",
    )
    assert result.fill_price == Decimal("10.010")
    assert result.commission == Decimal("1.001000")
    assert result.total_fee == Decimal("1.001000")
    assert result.stamp_duty == 0
    assert result.transfer_fee == 0
    assert result.exchange_fee == 0


def test_a_share_cost_model_charges_stamp_duty_on_sell_only() -> None:
    model = AShareCostFeeModel(
        commission_rate=Decimal("0.0003"),
        min_commission=Decimal("5"),
        stamp_duty_rate=Decimal("0.0005"),
        transfer_fee_rate=Decimal("0.00001"),
        exchange_fee_rate=Decimal("0.0000487"),
        slippage_bps=Decimal("2"),
    )
    buy = model.apply(reference_price=Decimal("10"), quantity=1000, side="buy")
    sell = model.apply(reference_price=Decimal("10"), quantity=1000, side="sell")

    assert buy.stamp_duty == 0
    assert sell.stamp_duty > 0
    assert sell.stamp_duty == Decimal("1000") * sell.fill_price * Decimal("0.0005")


def test_a_share_cost_model_applies_minimum_commission() -> None:
    model = AShareCostFeeModel(
        commission_rate=Decimal("0.0003"),
        min_commission=Decimal("5"),
    )
    # Tiny trade should hit minimum commission.
    result = model.apply(reference_price=Decimal("1"), quantity=100, side="buy")
    assert result.commission == Decimal("5")


def test_a_share_cost_model_includes_transfer_and_exchange_fees() -> None:
    model = AShareCostFeeModel(
        commission_rate=Decimal("0.0003"),
        transfer_fee_rate=Decimal("0.00001"),
        exchange_fee_rate=Decimal("0.0000487"),
    )
    result = model.apply(reference_price=Decimal("10"), quantity=1000, side="buy")
    assert result.transfer_fee > 0
    assert result.exchange_fee > 0
    assert result.total_fee == result.commission + result.transfer_fee + result.exchange_fee


def test_build_fee_model_defaults_to_linear_commission() -> None:
    model = build_fee_model()
    assert model.version == LINEAR_COMMISSION_V1


def test_build_fee_model_creates_a_share_model() -> None:
    model = build_fee_model(fee_model_version=A_SHARE_COST_V1)
    assert model.version == A_SHARE_COST_V1
    assert isinstance(model, AShareCostFeeModel)
    assert model.min_commission == Decimal("5")
    assert model.stamp_duty_rate == Decimal("0.0005")


def test_build_fee_model_rejects_a_share_components_for_linear() -> None:
    with pytest.raises(ValueError, match="linear_commission_v1 does not support"):
        build_fee_model(
            fee_model_version=LINEAR_COMMISSION_V1,
            min_commission=Decimal("5"),
        )


def test_build_fee_model_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="unsupported fee_model_version"):
        build_fee_model(fee_model_version="unknown_v1")


def test_a_share_factory_preserves_explicit_zero_cost_components() -> None:
    model = build_fee_model(
        fee_model_version=A_SHARE_COST_V1,
        commission_rate=Decimal("0"), slippage_bps=Decimal("0"),
        min_commission=Decimal("0"), sell_stamp_duty_rate=Decimal("0"),
        transfer_fee_rate=Decimal("0"), exchange_fee_rate=Decimal("0"),
    )
    assert model.min_commission == 0
    assert model.stamp_duty_rate == 0
    assert model.transfer_fee_rate == 0
    assert model.exchange_fee_rate == 0
    for side in ("buy", "sell"):
        assert model.apply(reference_price=Decimal("10"), quantity=100, side=side).total_fee == 0
