from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TradabilityCheck:
    can_buy: bool
    can_sell: bool
    reason: str | None = None


class AShareTradabilityPolicy:
    """A-share tradability rules for daily-event backtests.

    The policy is intentionally conservative so that simulated fills are
    defensible against real exchange tape:

    * suspended securities cannot trade in either direction;
    * a buy cannot fill if the open has already hit the upper limit (limit-up);
    * a sell cannot fill if the open has already hit the lower limit (limit-down).

    Limit prices are optional.  When they are missing the policy assumes the
    bar is tradable, because rejecting trades without data would silently
    over-optimise strategies.  Callers that have reliable limit-up/down fields
    should populate them; callers without them should treat the backtest as
    having an unbounded-tradability caveat.
    """

    DEFAULT_REASONS = {
        "suspended": "security is suspended",
        "limit_up": "open is at or above limit-up; buy unavailable",
        "limit_down": "open is at or below limit-down; sell unavailable",
    }

    def __init__(self, *, price_tolerance_bps: Decimal = Decimal("0")) -> None:
        self.price_tolerance = Decimal("1") + price_tolerance_bps / Decimal("10000")

    def check(self, bar: Any) -> TradabilityCheck:
        if getattr(bar, "is_suspended", False):
            return TradabilityCheck(
                can_buy=False,
                can_sell=False,
                reason=self.DEFAULT_REASONS["suspended"],
            )
        open_price = getattr(bar, "open", None)
        limit_up = getattr(bar, "limit_up", None)
        limit_down = getattr(bar, "limit_down", None)
        can_buy = not self._is_limit_up(open_price, limit_up)
        can_sell = not self._is_limit_down(open_price, limit_down)
        if not can_buy and not can_sell:
            return TradabilityCheck(
                can_buy=False,
                can_sell=False,
                reason=self.DEFAULT_REASONS["suspended"],
            )
        if not can_buy:
            return TradabilityCheck(
                can_buy=False,
                can_sell=True,
                reason=self.DEFAULT_REASONS["limit_up"],
            )
        if not can_sell:
            return TradabilityCheck(
                can_buy=True,
                can_sell=False,
                reason=self.DEFAULT_REASONS["limit_down"],
            )
        return TradabilityCheck(can_buy=True, can_sell=True, reason=None)

    def _is_limit_up(
        self,
        open_price: Decimal | None,
        limit_up: Decimal | None,
    ) -> bool:
        if open_price is None or limit_up is None:
            return False
        return open_price >= limit_up * self.price_tolerance

    def _is_limit_down(
        self,
        open_price: Decimal | None,
        limit_down: Decimal | None,
    ) -> bool:
        if open_price is None or limit_down is None:
            return False
        return open_price <= limit_down * self.price_tolerance
