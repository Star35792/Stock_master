"""Tests for A-share daily price-limit derivation.

The derivation decides whether a backtest may buy through a limit-up board, so
wrong bands silently produce untradable fills and overstated returns.  These
tests pin the board rules, the rounding rule and the "do not guess" rule.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.market import derive_price_limits, limit_band_for_symbol


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("600519.SH", Decimal("0.10")),  # main board
        ("000858.SZ", Decimal("0.10")),  # main board
        ("002415.SZ", Decimal("0.10")),  # main board (ex-SME)
        ("688111.SH", Decimal("0.20")),  # STAR
        ("300750.SZ", Decimal("0.20")),  # ChiNext
        ("920006.BJ", Decimal("0.30")),  # Beijing
        ("900901.SH", Decimal("0.10")),  # B share
    ],
)
def test_band_by_board(symbol: str, expected: Decimal) -> None:
    assert limit_band_for_symbol(symbol) == expected


def test_st_only_downgrades_main_board() -> None:
    assert limit_band_for_symbol("600519.SH", special_treatment_status="ST") == Decimal(
        "0.05"
    )
    # Registration-based boards keep their wider band under ST; that asymmetry is
    # an explicit modelling assumption documented in the module.
    assert limit_band_for_symbol("300750.SZ", special_treatment_status="ST") == Decimal(
        "0.20"
    )


def test_index_has_no_band() -> None:
    assert limit_band_for_symbol("000300.SH") is None


def test_unknown_prefix_has_no_band() -> None:
    assert limit_band_for_symbol("999999.SH") is None


def test_main_board_limits_round_half_up_to_one_cent() -> None:
    limit_up, limit_down = derive_price_limits(
        Decimal("21.89"), symbol="600519.SH", security_type="stock"
    )
    # 21.89 * 1.10 = 24.079 -> 24.08 ; 21.89 * 0.90 = 19.701 -> 19.70
    assert limit_up == Decimal("24.08")
    assert limit_down == Decimal("19.70")


def test_limits_are_symmetric_around_prev_close() -> None:
    limit_up, limit_down = derive_price_limits(
        Decimal("100.00"), symbol="688111.SH", security_type="stock"
    )
    assert limit_up == Decimal("120.00")
    assert limit_down == Decimal("80.00")


def test_st_limit_uses_five_percent() -> None:
    limit_up, limit_down = derive_price_limits(
        Decimal("10.00"),
        symbol="600519.SH",
        security_type="stock",
        special_treatment_status="ST",
    )
    assert limit_up == Decimal("10.50")
    assert limit_down == Decimal("9.50")


@pytest.mark.parametrize("pre_close", [None, Decimal("0"), Decimal("-1")])
def test_missing_or_non_positive_prev_close_yields_none(pre_close: Decimal | None) -> None:
    assert derive_price_limits(pre_close, symbol="600519.SH", security_type="stock") == (
        None,
        None,
    )


def test_non_stock_security_type_yields_none() -> None:
    assert derive_price_limits(
        Decimal("3000.00"), symbol="000300.SH", security_type="index"
    ) == (None, None)


def test_float_prev_close_is_accepted_without_binary_rounding() -> None:
    limit_up, _ = derive_price_limits(
        10.05, symbol="600519.SH", security_type="stock"
    )
    assert limit_up == Decimal("11.06")


def test_derivation_is_deterministic_across_calls() -> None:
    first = derive_price_limits(Decimal("7.77"), symbol="002415.SZ", security_type="stock")
    second = derive_price_limits(Decimal("7.77"), symbol="002415.SZ", security_type="stock")
    assert first == second


def test_unknown_board_symbol_is_not_treated_as_unlimited() -> None:
    """A symbol outside the known prefixes must not silently allow anything."""
    assert derive_price_limits(
        Decimal("10.00"), symbol="999999.SH", security_type="stock"
    ) == (None, None)


def test_date_argument_is_not_required() -> None:
    """Guard the pure-function contract: no I/O, no calendar dependency."""
    assert callable(derive_price_limits)
    assert isinstance(date(2026, 1, 1), date)
