"""Derive A-share daily price limits from point-in-time ``pre_close``.

Why this module exists
----------------------

``AShareTradabilityPolicy`` rejects buys at limit-up and sells at limit-down,
but only when the bar carries ``limit_up`` / ``limit_down``.  In practice those
columns were never populated, so ``_is_limit_up`` / ``_is_limit_down`` returned
``False`` and every bar behaved as freely tradable.  A multi-year backtest that
can buy through one-word limit-up boards systematically overstates returns,
especially for low-priced / low-liquidity names -- exactly the segment that
bound strategies such as "oversold rebound" tend to select.

Derivation rules (explicit research assumptions)
------------------------------------------------

* The anchor is the bar's **own** ``pre_close`` field, never the previous row's
  ``close``.  On an ex-dividend / ex-rights day the previous close is not the
  exchange's previous settlement price, so deriving from ``close`` introduces a
  systematic mis-pricing precisely on corporate-action days.
* Board limits follow the code prefix of the symbol:

  =============  ==========================================  ==========
  symbol prefix  board                                       limit
  =============  ==========================================  ==========
  600/601/603/605, 000/001/002/003, 900/200  main board / B  +/- 10%
  688                                        STAR market      +/- 20%
  300/301                                    ChiNext          +/- 20%
  43/83/87/88/920                            Beijing (BSE)    +/- 30%
  =============  ==========================================  ==========

* ``special_treatment_status`` downgrades **main-board** ST / *ST to +/- 5%.
  Registration-based boards (STAR, ChiNext) keep their +/- 20% band even under
  ST; that asymmetry is an explicit modelling assumption, not an exchange
  quote.
* Indexes, ETFs and any non-``stock`` security type have no applicable band and
  yield ``(None, None)``; the caller decides whether that means "tradable"
  (legacy behaviour) or "reject" (fail-closed).
* Results are rounded **half-up to 0.01**, matching the A-share tick size, so
  that engine-side tolerance comparisons behave deterministically.
* A missing or non-positive ``pre_close`` yields ``(None, None)`` instead of a
  guessed value.  Newly listed issues have no meaningful previous price and are
  therefore excluded from limit enforcement rather than silently allowed.

This module is a pure function layer: it performs no I/O so it can be unit
tested and reused by both the warm-layer read path and the control-plane
backfill script, guaranteeing a single derivation rule across sources.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "PRICE_LIMIT_RULES",
    "PriceLimitRule",
    "derive_price_limits",
    "limit_band_for_symbol",
]

_CENT = Decimal("0.01")
_ZERO = Decimal("0")

#: Ordered (prefix tuple, band, ten-percent ST band) rules.  Order matters:
#: longer / more specific prefixes must precede shorter ones.
PRICE_LIMIT_RULES: tuple[PriceLimitRule, ...] | None = None  # set below


class PriceLimitRule:
    """A single board price-limit band."""

    __slots__ = ("band", "board", "prefixes", "st_band")

    def __init__(
        self,
        board: str,
        prefixes: tuple[str, ...],
        band: Decimal,
        st_band: Decimal | None = None,
    ) -> None:
        self.board = board
        self.prefixes = prefixes
        self.band = band
        self.st_band = st_band

    def matches(self, symbol: str) -> bool:
        code = symbol.split(".")[0]
        return any(code.startswith(prefix) for prefix in self.prefixes)


PRICE_LIMIT_RULES = (
    PriceLimitRule("star", ("688",), Decimal("0.20")),
    PriceLimitRule("chinext", ("300", "301"), Decimal("0.20")),
    PriceLimitRule("bse", ("43", "83", "87", "88", "920"), Decimal("0.30")),
    PriceLimitRule(
        "b_share",
        ("900", "200"),
        Decimal("0.10"),
        st_band=Decimal("0.05"),
    ),
    PriceLimitRule(
        "main",
        ("600", "601", "603", "605", "000", "001", "002", "003"),
        Decimal("0.10"),
        st_band=Decimal("0.05"),
    ),
)

#: Statuses that identify ST / *ST treatment in ``special_treatment_status``.
ST_STATUSES = frozenset({"ST", "*ST", "st", "S*ST", "SST"})


def limit_band_for_symbol(
    symbol: str,
    *,
    special_treatment_status: str | None = None,
    security_type: str | None = None,
) -> Decimal | None:
    """Return the absolute price band (e.g. ``0.10``) or ``None`` if not applicable.

    ``000xxx.SH`` is excluded explicitly: on the Shanghai exchange that prefix
    belongs to indices (SSE Composite ``000001.SH``, CSI 300 ``000300.SH``)
    rather than main-board stocks, and an index has no price limit.  The same
    prefix on Shenzhen (``000001.SZ``) is a genuine main-board stock.
    """
    if security_type is not None and security_type != "stock":
        return None
    code = symbol.split(".")[0]
    if not code.isdigit():
        return None
    if symbol.upper().endswith(".SH") and code.startswith("000"):
        return None
    is_st = (special_treatment_status or "").strip() in ST_STATUSES
    for rule in PRICE_LIMIT_RULES:
        if not rule.matches(symbol):
            continue
        if is_st and rule.st_band is not None:
            return rule.st_band
        return rule.band
    return None


def _round_to_cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def derive_price_limits(
    pre_close: Decimal | float | None,
    *,
    symbol: str,
    security_type: str | None = None,
    special_treatment_status: str | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    """Return ``(limit_up, limit_down)`` for a single bar.

    ``(None, None)`` is returned when no band applies (index / ETF / unknown
    board) or when ``pre_close`` is missing or non-positive.  Callers must not
    interpret ``None`` as "unrestricted"; see the module docstring.
    """
    if security_type is not None and security_type != "stock":
        return None, None
    if pre_close is None:
        return None, None
    base = pre_close if isinstance(pre_close, Decimal) else Decimal(str(pre_close))
    if base <= _ZERO:
        return None, None

    band = limit_band_for_symbol(
        symbol,
        special_treatment_status=special_treatment_status,
        security_type=security_type,
    )
    if band is None:
        return None, None

    step = _round_to_cent(base * band)
    return _round_to_cent(base + step), _round_to_cent(base - step)
