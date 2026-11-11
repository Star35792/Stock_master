"""跨源对账口径规则的单元测试。

这些规则把实测知识固化成了代码：舍入容差、成交量单位差归一、份额折算、
成交额优先。每一条都对应一个真实案例，改动它们必须先改这里。
"""

from decimal import Decimal

from app.services.reconciliation_rules import (
    AMOUNT_REL_TOLERANCE,
    PRICE_ABS_TOLERANCE,
    VOLUME_SHARE_FOLD_BAND,
    compare_amount,
    compare_close,
    compare_daily_bar_fields,
    compare_ohlc,
    compare_volume,
)


def test_close_within_absolute_tolerance_is_not_conflict() -> None:
    # 实测：通达信与项目在 6,125 个可比标的中最大舍入差 0.005 元。
    result = compare_close(Decimal("3663.7200"), Decimal("3663.7235"))

    assert result.is_conflict is False


def test_close_beyond_both_tolerances_is_conflict() -> None:
    # 相对差 1% 远超 PRICE_REL_TOLERANCE，绝对差也远超 PRICE_ABS_TOLERANCE。
    result = compare_close(Decimal("100.00"), Decimal("101.00"))

    assert result.is_conflict is True
    assert "close differs by" in (result.note or "")


def test_close_difference_beyond_absolute_tolerance_is_conflict_when_comparable() -> None:
    """茅台案例：成交额完全一致，收盘价却差 0.53 元 —— 真实错价。

    成交额一致意味着均价一致，价格不该差 0.53 元。此时只允许绝对舍入容差，
    相对容差不适用 —— 否则 0.043% 的错价会被放过。
    """
    result = compare_close(Decimal("1242.47"), Decimal("1243.00"), comparable=True)

    assert result.is_conflict is True
    assert "close differs by 0.53" in (result.note or "")


def test_high_price_precision_difference_is_tolerated_when_not_comparable() -> None:
    """高价位标的的相对容差（仅在两边口径不可比时适用）。

    实测三只 LOF 的收盘价绝对差高达 8.32 元，但相对差只有 0.15% —— 那是
    份额折算的副作用，不是价格记错。
    """
    result = compare_close(
        Decimal("5462.52"), Decimal("5470.8402"), comparable=False
    )

    assert result.is_conflict is False
    assert (result.raw_rel_diff or Decimal("1")) < Decimal("0.005")


def test_high_price_difference_is_conflict_even_when_not_comparable() -> None:
    """口径不可比时，相对容差之外仍要抓出来。"""
    result = compare_close(
        Decimal("5462.52"), Decimal("5600.00"), comparable=False
    )

    assert result.is_conflict is True


def test_low_price_absolute_error_is_still_caught() -> None:
    """只看相对差会漏掉低价股的真实错误 —— 绝对容差必须保留。"""
    result = compare_close(Decimal("1.00"), Decimal("1.05"))

    assert result.is_conflict is True


def test_ohlc_catches_error_hidden_in_open_only() -> None:
    """收盘价正确但开盘价记错的情况不能漏判。"""
    result = compare_ohlc(
        {"open": Decimal("100.00"), "high": Decimal("101.00"),
         "low": Decimal("99.00"), "close": Decimal("100.50")},
        {"open": Decimal("103.00"), "high": Decimal("101.00"),
         "low": Decimal("99.00"), "close": Decimal("100.50")},
    )

    assert result.is_conflict is True
    assert result.differing_fields == ("open",)


def test_ohlc_accepts_matching_prices() -> None:
    result = compare_ohlc(
        {"open": Decimal("100.00"), "high": Decimal("101.00"),
         "low": Decimal("99.00"), "close": Decimal("100.50")},
        {"open": Decimal("100.00"), "high": Decimal("101.00"),
         "low": Decimal("99.00"), "close": Decimal("100.50")},
    )

    assert result.is_conflict is False
    assert result.differing_fields == ()


def test_volume_equal_is_consistent() -> None:
    result = compare_volume(Decimal("1000"), Decimal("1000"))

    assert result.is_conflict is False
    assert result.is_unit_adjusted is False


def test_volume_unit_hundred_is_calibrated_not_conflict() -> None:
    """实测：541 只指数差精确 100 倍（通达信「手」vs 项目「股」）。"""
    result = compare_volume(Decimal("40486517700"), Decimal("404865177"))

    assert result.is_conflict is False
    assert result.is_unit_adjusted is True
    assert "x100" in (result.note or "")


def test_volume_unit_ten_thousand_is_calibrated_not_conflict() -> None:
    """实测：2 只指数差精确 10000 倍。"""
    result = compare_volume(Decimal("10000000"), Decimal("1000"))

    assert result.is_conflict is False
    assert result.is_unit_adjusted is True


def test_volume_share_fold_band_is_calibrated_not_conflict() -> None:
    """实测：LOF 基金 000170.SH 成交量比 105.4166 倍（基金份额折算）。"""
    result = compare_volume(Decimal("2184036300"), Decimal("20718148"))

    assert result.is_conflict is False
    assert result.is_unit_adjusted is True


def test_volume_outside_share_fold_band_is_conflict() -> None:
    """实测：399002.SZ「深成指R」比 65.75 倍，落在窄带外 —— 是真实问题。"""
    result = compare_volume(Decimal("34633771000"), Decimal("526723497"))

    assert result.is_conflict is True


def test_volume_share_fold_band_bounds_match_documented_range() -> None:
    low, high = VOLUME_SHARE_FOLD_BAND

    assert low == Decimal("90")
    assert high == Decimal("120")


def test_amount_equal_is_consistent() -> None:
    result = compare_amount(Decimal("673951890000"), Decimal("673951890000"))

    assert result.is_conflict is False


def test_amount_beyond_tolerance_is_conflict() -> None:
    """实测：399002.SZ「深成指R」成交额差 50.4%。"""
    result = compare_amount(Decimal("412372110000"), Decimal("830819926016"))

    assert result.is_conflict is True
    assert (result.raw_rel_diff or Decimal("0")) > Decimal("0.5")


def test_amount_not_comparable_when_volume_unit_differs() -> None:
    """份额折算成立时成交额不可直接比 —— 联动规则。"""
    result = compare_amount(
        Decimal("74212911000"),
        Decimal("73075384358.5"),
        comparable=False,
    )

    assert result.is_conflict is False


def test_amount_tolerance_matches_documented_value() -> None:
    assert AMOUNT_REL_TOLERANCE == Decimal("0.01")


def test_price_absolute_tolerance_matches_documented_value() -> None:
    assert PRICE_ABS_TOLERANCE == Decimal("0.011")


def test_daily_bar_fields_treats_share_fold_amount_as_non_conflict() -> None:
    """三只 LOF 的成交额差（1.6% / 8.1% / 13.6%）源自同一个份额折算。

    实测 2026-11-10，这三只标的不应被判为冲突 —— 它们是口径差而非数据错。
    """
    fields = compare_daily_bar_fields(
        Decimal("5549.54"),
        Decimal("5551.3679"),
        Decimal("2184036300"),
        Decimal("20718148"),
        Decimal("74212911000"),
        Decimal("73075384358.5"),
    )

    assert fields["volume"].is_unit_adjusted is True
    assert fields["amount"].is_conflict is False
    assert fields["close"].is_conflict is False


def test_daily_bar_fields_catches_price_error_when_amount_agrees() -> None:
    """茅台案例：成交额完全一致，收盘价差 0.53 元 —— 真实错价必须抓出。"""
    fields = compare_daily_bar_fields(
        Decimal("1242.47"),
        Decimal("1243.00"),
        Decimal("1000"),
        Decimal("1000"),
        Decimal("1242470"),
        Decimal("1242470"),
    )

    assert fields["amount"].is_conflict is False
    assert fields["close"].is_conflict is True


def test_daily_bar_fields_catches_real_amount_inconsistency() -> None:
    """399002.SZ「深成指R」成交额差 50.4% —— 真实的数据错误必须被抓出。"""
    fields = compare_daily_bar_fields(
        Decimal("15286.7070"),
        Decimal("15286.7073"),
        Decimal("34633771000"),
        Decimal("526723497"),
        Decimal("412372110000"),
        Decimal("830819926016"),
    )

    assert fields["amount"].is_conflict is True
    assert fields["close"].is_conflict is False
