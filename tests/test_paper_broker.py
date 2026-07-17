from datetime import date
from decimal import Decimal

from app.trade import BrokerOrderRequest, PaperBrokerAdapter


def test_paper_broker_applies_slippage_and_commission() -> None:
    fill = PaperBrokerAdapter(
        commission_rate=Decimal("0.001"),
        slippage_bps=Decimal("10"),
    ).execute(
        BrokerOrderRequest(
            symbol="AAA",
            side="buy",
            quantity=100,
            reference_price=Decimal("10"),
            trade_date=date(2026, 7, 17),
        )
    )

    assert fill.price == Decimal("10.010")
    assert fill.commission == Decimal("1.001000")
