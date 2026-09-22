"""Deterministic FCFE research calculations, without trading signals or I/O."""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from app.backtest.fees import AShareCostFeeModel
from app.schemas.price_value import PriceValueFees, PriceValueRequest

VALUATION_QUANTUM = Decimal("0.0000000001")


def calculate_valuation(request: PriceValueRequest) -> dict[str, Decimal]:
    # FCFE is already cash flow to equity. Do not subtract debt a second time.
    with localcontext() as context:
        context.prec = 80
        values = {}
        for name in ("low", "base", "high"):
            scenario = getattr(request.scenarios, name)
            discount = Decimal(1) + scenario.cost_of_equity
            present_value = sum(
                (
                    cash_flow / discount**year
                    for year, cash_flow in enumerate(scenario.cash_flows, 1)
                ),
                Decimal(),
            )
            # terminal_cash_flow is the explicit year N+1 amount, not year N.
            terminal_value = scenario.terminal_cash_flow / (
                scenario.cost_of_equity - scenario.terminal_growth
            )
            present_value += terminal_value / discount ** len(scenario.cash_flows)
            # Published per-share values have a fixed ten-decimal contract.
            # All downstream price decisions consume this exact published value.
            values[name] = (present_value / request.diluted_shares).quantize(
                VALUATION_QUANTUM, rounding=ROUND_HALF_UP
            )
            if values[name] <= 0:
                raise ValueError(
                    "scenario per-share value must be positive at ten-decimal precision"
                )
        if not values["low"] <= values["base"] <= values["high"]:
            raise ValueError("scenario per-share values must satisfy low <= base <= high")
        return values


def calculate_price_metrics(
    quote: Decimal,
    low: Decimal,
    required_margin: Decimal,
    quantity: int,
    fees: PriceValueFees,
) -> dict[str, Decimal | bool]:
    if not all(
        isinstance(value, Decimal) and value.is_finite() for value in (quote, low, required_margin)
    ):
        raise ValueError("prices and margin must be finite Decimals")
    if quote <= 0 or low <= 0 or not 0 < required_margin < 1:
        raise ValueError("prices must be positive and margin must be in (0, 1)")
    if (
        isinstance(quantity, bool)
        or not isinstance(quantity, int)
        or not 0 < quantity <= 1000000000
    ):
        raise ValueError("quantity must be a positive bounded integer")
    with localcontext() as context:
        context.prec = 80
        fee_model = AShareCostFeeModel(
            commission_rate=fees.commission_rate,
            min_commission=fees.min_commission,
            stamp_duty_rate=Decimal(0),
            transfer_fee_rate=fees.transfer_fee_rate,
            exchange_fee_rate=fees.exchange_fee_rate,
            slippage_bps=fees.slippage_bps,
        )
        result = fee_model.apply(reference_price=quote, quantity=quantity, side="buy")
        all_in_price = result.fill_price + result.total_fee / Decimal(quantity)
        ceiling = low * (Decimal(1) - required_margin)
        # Search integer price ticks so rounding and minimum commission agree
        # exactly with the existing fee engine; zero means no positive quote fits.
        lower = 0
        upper = int((ceiling * 100).to_integral_value(rounding=ROUND_FLOOR))
        while lower < upper:
            middle = (lower + upper + 1) // 2
            candidate = fee_model.apply(
                reference_price=Decimal(middle) / 100, quantity=quantity, side="buy"
            )
            if candidate.fill_price + candidate.total_fee / Decimal(quantity) <= ceiling:
                lower = middle
            else:
                upper = middle - 1
        return {
            "gross_margin": Decimal(1) - quote / low,
            "cost_adjusted_margin": Decimal(1) - all_in_price / low,
            "buy_cost_ceiling": ceiling,
            "max_buy_quote": Decimal(lower) / 100,
            "all_in_price": all_in_price,
            "fee_total": result.total_fee,
            "slippage_cost": result.slippage_cost,
            "price_condition_met": all_in_price <= ceiling,
        }
