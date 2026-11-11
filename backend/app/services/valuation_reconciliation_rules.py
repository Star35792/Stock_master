"""Cross-source reconciliation rules for valuation observations.

Why a dedicated rule set
------------------------
Two vendors publish the same field names with different meanings.  Comparing
them field by field without knowing the caliber produces either a flood of
false conflicts or -- far worse -- a silent "everything agrees" verdict that
hides a real discrepancy.  This module encodes what was measured so the
comparison is explicit, testable and auditable.

Measured evidence (2026-11-11, Eastmoney clist vs Tencent quote, whole market)
---------------------------------------------------------------------------
1. **Eastmoney ``f9`` is a static PE, not a TTM PE.**  Against Tencent's
   ``pe_static`` the two agree within 1% for 5,575 of 5,576 symbols
   (99.98%); against Tencent's ``pe_ttm`` they agree within 1% for only
   2.47%.  A naive "compare the PE columns" therefore compares two different
   quantities and reports a median gap of 18% that is pure caliber.

2. **PB agrees exactly for most symbols.**  5,292 of 5,576 symbols (94.9%)
   have a bit-identical PB on both sides.  The tail is not noise: it is a
   *denominator* difference (which net-asset figure the vendor divides by),
   which shows up as a ratio, never as a small jitter.

3. **Market capitalisation agrees for 99.9% of symbols** within 2%, and the
   closing price for 99.9% within 0.1%.

4. **Index codes collide with the stock code space.**  108 six-digit codes are
   index codes on the Eastmoney side while the vendor quotes a *company* under
   the same digits (for example ``000905``: CSI 500 on one side, a listed
   company on the other).  Comparing them produces a 99.9% "price
   difference" that is a symbol-space collision, not a data error.

Caliber rules encoded here
---------------------------
* ``pe_ttm`` and a static PE are **different quantities** and are never
  compared against each other.
* PB is compared with a **relative tolerance**, because a caliber difference
  shows up as a ratio.
* A price or market-cap comparison first requires the symbol to be on both
  sides; a one-sided symbol is not a conflict, it is a coverage gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

#: Relative tolerance for PB.  The denominator caliber difference is a ratio,
#: and the measured exact-match rate is 94.9%; a 2% tolerance keeps the
#: genuine caliber differences visible while absorbing vendor rounding.
PB_REL_TOLERANCE = Decimal("0.02")

#: Relative tolerance for total market capitalisation.  Measured: 99.9% of
#: symbols agree within 2%, and the unit (yuan) is unambiguous on both sides.
MARKET_CAP_REL_TOLERANCE = Decimal("0.02")

#: Relative tolerance for the closing price.  Measured: 99.9% of symbols agree
#: within 0.1%.  The absolute floor absorbs sub-cent rounding.
PRICE_REL_TOLERANCE = Decimal("0.001")
PRICE_ABS_TOLERANCE = Decimal("0.011")

#: Relative tolerance for the static PE.  Used only when both sides publish a
#: *static* PE, which is the one comparable pair the two vendors share.
STATIC_PE_REL_TOLERANCE = Decimal("0.01")


class PeCaliber(StrEnum):
    """Which PE caliber a value belongs to."""

    TTM = "ttm"
    STATIC = "static"
    UNKNOWN = "unknown"


class ValuationComparison(StrEnum):
    """Outcome of comparing one field across two sources."""

    MATCH = "match"
    CONFLICT = "conflict"
    NOT_COMPARABLE = "not_comparable"
    COVERAGE_GAP = "coverage_gap"


@dataclass(frozen=True)
class FieldVerdict:
    """One field's cross-source verdict."""

    comparison: ValuationComparison
    rel_diff: Decimal | None = None
    abs_diff: Decimal | None = None
    note: str | None = None

    @property
    def is_conflict(self) -> bool:
        return self.comparison is ValuationComparison.CONFLICT


def _relative(left: Decimal | None, right: Decimal | None) -> Decimal | None:
    if left is None or right is None:
        return None
    if right == 0:
        return None
    return abs(left - right) / abs(right)


def compare_price(
    left: Decimal | None,
    right: Decimal | None,
) -> FieldVerdict:
    """Compare closing prices with an absolute-plus-relative tolerance."""
    if left is None or right is None:
        return FieldVerdict(ValuationComparison.COVERAGE_GAP)
    diff = abs(left - right)
    rel = _relative(left, right)
    if diff <= PRICE_ABS_TOLERANCE:
        return FieldVerdict(ValuationComparison.MATCH, rel, diff)
    if rel is not None and rel <= PRICE_REL_TOLERANCE:
        return FieldVerdict(ValuationComparison.MATCH, rel, diff)
    note = (
        f"price differs by {diff} (rel {rel:.6%})"
        if rel is not None
        else f"price differs by {diff}"
    )
    return FieldVerdict(ValuationComparison.CONFLICT, rel, diff, note)


def compare_market_cap(
    left: Decimal | None,
    right: Decimal | None,
) -> FieldVerdict:
    """Compare total market capitalisation; both sides are in yuan."""
    if left is None or right is None:
        return FieldVerdict(ValuationComparison.COVERAGE_GAP)
    rel = _relative(left, right)
    if rel is None:
        return FieldVerdict(ValuationComparison.NOT_COMPARABLE)
    if rel <= MARKET_CAP_REL_TOLERANCE:
        return FieldVerdict(ValuationComparison.MATCH, rel)
    return FieldVerdict(
        ValuationComparison.CONFLICT,
        rel,
        note=f"market cap differs by {rel:.6%}",
    )


def compare_pb(
    left: Decimal | None,
    right: Decimal | None,
) -> FieldVerdict:
    """Compare price-to-book.

    PB's denominator caliber (which net-asset figure is used) shows up as a
    ratio rather than as jitter, so the comparison is relative.  A difference
    beyond the tolerance is reported as a conflict for review -- it is a real
    caliber difference, and a reviewer has to decide which side is right.
    """
    if left is None or right is None:
        return FieldVerdict(ValuationComparison.COVERAGE_GAP)
    rel = _relative(left, right)
    if rel is None:
        return FieldVerdict(ValuationComparison.NOT_COMPARABLE)
    if rel <= PB_REL_TOLERANCE:
        return FieldVerdict(ValuationComparison.MATCH, rel)
    return FieldVerdict(
        ValuationComparison.CONFLICT,
        rel,
        note=f"pb differs by {rel:.6%} (likely net-asset denominator caliber)",
    )


def compare_pe(
    left: Decimal | None,
    right: Decimal | None,
    *,
    left_caliber: PeCaliber,
    right_caliber: PeCaliber,
) -> FieldVerdict:
    """Compare PE values, refusing to compare across calibers.

    A trailing PE and a static PE are different quantities.  Comparing them
    produces a large, entirely spurious difference, so the rule reports them
    as *not comparable* instead of as a conflict.
    """
    if left is None or right is None:
        return FieldVerdict(ValuationComparison.COVERAGE_GAP)
    if left_caliber is PeCaliber.UNKNOWN or right_caliber is PeCaliber.UNKNOWN:
        return FieldVerdict(
            ValuationComparison.NOT_COMPARABLE,
            note="pe caliber unknown on one side",
        )
    if left_caliber is not right_caliber:
        return FieldVerdict(
            ValuationComparison.NOT_COMPARABLE,
            note=(
                f"pe caliber differs: {left_caliber} vs {right_caliber}"
            ),
        )
    if left_caliber is not PeCaliber.STATIC:
        # Two trailing PEs on the same day should agree closely, but the
        # reporting-period cut-off is not certified on either side, so a
        # difference is only a conflict when it is large.
        rel = _relative(left, right)
        if rel is None:
            return FieldVerdict(ValuationComparison.NOT_COMPARABLE)
        if rel <= Decimal("0.05"):
            return FieldVerdict(ValuationComparison.MATCH, rel)
        return FieldVerdict(
            ValuationComparison.CONFLICT,
            rel,
            note=f"ttm pe differs by {rel:.6%}",
        )
    rel = _relative(left, right)
    if rel is None:
        return FieldVerdict(ValuationComparison.NOT_COMPARABLE)
    if rel <= STATIC_PE_REL_TOLERANCE:
        return FieldVerdict(ValuationComparison.MATCH, rel)
    return FieldVerdict(
        ValuationComparison.CONFLICT,
        rel,
        note=f"static pe differs by {rel:.6%}",
    )


def detect_symbol_collision(
    left_symbol: str,
    right_symbol: str,
    *,
    left_name: str | None = None,
    right_name: str | None = None,
) -> bool:
    """Detect that two codes that look equal refer to different instruments.

    The index code space overlaps the stock code space, so the same six digits
    can be a CSI index on one side and a listed company on the other.  A
    grossly different name is the tell.
    """
    if left_name and right_name and left_name.strip() != right_name.strip():
        return True
    return left_symbol != right_symbol


__all__ = [
    "FieldVerdict",
    "MARKET_CAP_REL_TOLERANCE",
    "PB_REL_TOLERANCE",
    "PRICE_ABS_TOLERANCE",
    "PRICE_REL_TOLERANCE",
    "PeCaliber",
    "STATIC_PE_REL_TOLERANCE",
    "ValuationComparison",
    "compare_market_cap",
    "compare_pb",
    "compare_pe",
    "compare_price",
    "detect_symbol_collision",
]
