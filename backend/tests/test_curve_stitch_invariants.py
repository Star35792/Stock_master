"""Fail-closed invariants for rolling combined-equity curves."""

from datetime import date
from decimal import Decimal

import pytest

from app.backtest.curve_stitch import (
    WindowCostSummary,
    stitch_equity_curves,
)

INITIAL_CASH = Decimal("100000")
COST = WindowCostSummary(commission=Decimal(), traded_notional=Decimal())


def test_stitch_rejects_non_positive_initial_cash() -> None:
    curve = [(date(2026, 1, 2), INITIAL_CASH)]

    with pytest.raises(ValueError, match="initial_cash must be positive"):
        stitch_equity_curves([curve], [COST], Decimal())


@pytest.mark.parametrize("equity", [Decimal(), Decimal("-1")])
def test_stitch_rejects_non_positive_equity(equity: Decimal) -> None:
    curve = [(date(2026, 1, 2), equity)]

    with pytest.raises(ValueError, match="equity values must be positive"):
        stitch_equity_curves([curve], [COST], INITIAL_CASH)


@pytest.mark.parametrize(
    "curve",
    [
        [
            (date(2026, 1, 3), INITIAL_CASH),
            (date(2026, 1, 2), INITIAL_CASH),
        ],
        [
            (date(2026, 1, 2), INITIAL_CASH),
            (date(2026, 1, 2), INITIAL_CASH),
        ],
    ],
)
def test_stitch_rejects_non_increasing_dates(
    curve: list[tuple[date, Decimal]],
) -> None:
    with pytest.raises(ValueError, match="trade dates must be strictly increasing"):
        stitch_equity_curves([curve], [COST], INITIAL_CASH)
