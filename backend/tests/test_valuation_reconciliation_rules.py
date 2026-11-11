"""Tests for the cross-source valuation reconciliation rules.

The caliber facts encoded here were measured against the whole market on
2026-11-11.  They are locked in here because each of them was a way to
produce a wrong answer:

* comparing a trailing PE with a static PE looks like a data conflict but is
  really a caliber difference;
* comparing index codes with stock codes looks like a 99.9% price error but
  is really a symbol-space collision;
* a one-tick absolute price tolerance is 0.44% on a 2.27-yuan stock, which
  is wide enough to let a whole wrong session through.
"""

from decimal import Decimal

from app.services.tencent_valuation_collection import price_matches_trusted_close
from app.services.valuation_reconciliation_rules import (
    PeCaliber,
    ValuationComparison,
    compare_market_cap,
    compare_pb,
    compare_pe,
    compare_price,
    detect_symbol_collision,
)


class TestComparePrice:
    def test_one_tick_rounding_difference_is_a_match(self) -> None:
        # Measured: the two vendors round to the tick differently.
        result = compare_price(Decimal("1439.00"), Decimal("1439.01"))

        assert result.comparison is ValuationComparison.MATCH
        assert result.is_conflict is False

    def test_sub_cent_absolute_tolerance_covers_a_high_price(self) -> None:
        # A high-priced stock moves more in relative terms for the same tick.
        result = compare_price(Decimal("1402.30"), Decimal("1402.31"))

        assert result.comparison is ValuationComparison.MATCH

    def test_a_real_price_difference_is_a_conflict(self) -> None:
        # The index-code collision case: three orders of magnitude apart.
        result = compare_price(Decimal("8.26"), Decimal("6713.18"))

        assert result.comparison is ValuationComparison.CONFLICT
        assert result.is_conflict is True

    def test_a_missing_side_is_a_coverage_gap_not_a_conflict(self) -> None:
        result = compare_price(Decimal("10.00"), None)

        assert result.comparison is ValuationComparison.COVERAGE_GAP
        assert result.is_conflict is False


class TestCompareMarketCap:
    def test_equal_caps_match(self) -> None:
        result = compare_market_cap(
            Decimal("1752989000000"), Decimal("1752989000000")
        )

        assert result.comparison is ValuationComparison.MATCH

    def test_unit_scale_difference_is_a_conflict(self) -> None:
        # One side in yuan, the other in hundred million yuan.
        result = compare_market_cap(Decimal("1752989000000"), Decimal("17529.89"))

        assert result.comparison is ValuationComparison.CONFLICT


class TestComparePb:
    def test_exactly_equal_pb_matches(self) -> None:
        # Measured: 5,292 of 5,576 symbols share a bit-identical PB.
        result = compare_pb(Decimal("1.66"), Decimal("1.66"))

        assert result.comparison is ValuationComparison.MATCH
        assert result.rel_diff == Decimal("0")

    def test_a_denominator_caliber_difference_is_reported_as_conflict(self) -> None:
        # Measured: 2.8% of the market differs by more than 2%, spread evenly
        # across the exchanges, so it is a net-asset caliber difference rather
        # than one broken exchange.
        result = compare_pb(Decimal("2.65"), Decimal("2.48"))

        assert result.comparison is ValuationComparison.CONFLICT
        assert "caliber" in (result.note or "")


class TestComparePe:
    def test_a_static_pe_matches_within_one_percent(self) -> None:
        # Measured: Eastmoney f9 against Tencent pe_static agrees for
        # 5,575 of 5,576 symbols.
        result = compare_pe(
            Decimal("75.80"),
            Decimal("75.80"),
            left_caliber=PeCaliber.STATIC,
            right_caliber=PeCaliber.STATIC,
        )

        assert result.comparison is ValuationComparison.MATCH

    def test_trailing_and_static_pe_are_not_comparable(self) -> None:
        # The whole point: these are different quantities, so a large
        # difference is not a data conflict.
        result = compare_pe(
            Decimal("75.80"),
            Decimal("20.39"),
            left_caliber=PeCaliber.STATIC,
            right_caliber=PeCaliber.TTM,
        )

        assert result.comparison is ValuationComparison.NOT_COMPARABLE
        assert result.is_conflict is False
        assert "caliber differs" in (result.note or "")

    def test_two_trailing_pe_values_that_differ_widely_are_a_conflict(self) -> None:
        result = compare_pe(
            Decimal("20.00"),
            Decimal("30.00"),
            left_caliber=PeCaliber.TTM,
            right_caliber=PeCaliber.TTM,
        )

        assert result.comparison is ValuationComparison.CONFLICT

    def test_an_unknown_caliber_is_not_comparable(self) -> None:
        result = compare_pe(
            Decimal("20.00"),
            Decimal("30.00"),
            left_caliber=PeCaliber.UNKNOWN,
            right_caliber=PeCaliber.TTM,
        )

        assert result.comparison is ValuationComparison.NOT_COMPARABLE


class TestSymbolCollision:
    def test_a_different_name_indicates_a_collision(self) -> None:
        assert detect_symbol_collision(
            "000905.SZ", "000905.SZ", left_name="中证500", right_name="某上市公司"
        ) is True

    def test_the_same_name_is_not_a_collision(self) -> None:
        assert detect_symbol_collision(
            "600519.SH", "600519.SH", left_name="贵州茅台", right_name="贵州茅台"
        ) is False


class TestPriceGate:
    def test_an_exact_match_passes(self) -> None:
        assert price_matches_trusted_close(Decimal("1439.00"), Decimal("1439.00"))

    def test_a_one_tick_difference_on_a_high_price_passes(self) -> None:
        assert price_matches_trusted_close(Decimal("1402.30"), Decimal("1402.31"))

    def test_a_one_tick_difference_on_a_low_price_fails(self) -> None:
        # 0.01 yuan on a 2.27-yuan stock is 0.44% -- wider than the relative
        # tolerance.  Measured: this exact leak let a whole wrong session
        # through for 196 low-priced symbols.
        assert not price_matches_trusted_close(Decimal("2.28"), Decimal("2.27"))

    def test_a_large_difference_fails(self) -> None:
        assert not price_matches_trusted_close(Decimal("8.26"), Decimal("6713.18"))
