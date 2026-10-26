"""Tests for the special-treatment status derivation.

The status is derived from the security's short name.  Getting this wrong
in either direction is costly: a missed ST security gets the normal +/-10%
price band and can be bought on a limit-up board, while a false positive
gets a +/-5% band that does not exist and blocks tradable securities.
"""

from __future__ import annotations

import pytest

from app.market.special_treatment import (
    DELISTING_STATUS,
    NORMAL_STATUS,
    ST_STATUS,
    derive_special_treatment_status,
    is_special_treatment,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("*ST美丽", ST_STATUS),
        ("ST海王", ST_STATUS),
        ("ST晨鸣", ST_STATUS),
        ("*ST中迪", ST_STATUS),
        ("*ST西旅", ST_STATUS),
        ("*ST华新", ST_STATUS),
        ("S*ST华新", ST_STATUS),
        ("SST华新", ST_STATUS),
        ("*ST康佳A", ST_STATUS),
        ("ST华仪", ST_STATUS),
    ],
)
def test_st_markers_are_detected(name: str, expected: str) -> None:
    assert derive_special_treatment_status(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "平安银行",
        "万科A",
        "招商港口",
        "中国平安",
        "XD平安",
        "DR紫光",
        "三六零",
    ],
)
def test_ordinary_names_are_normal(name: str) -> None:
    assert derive_special_treatment_status(name) == NORMAL_STATUS


@pytest.mark.parametrize(
    "name",
    ["国华退", "恒久退", "赛隆退", "天龙退", "立方退", "退市创兴"],
)
def test_delisting_marker_is_not_st(name: str) -> None:
    """A delisting security is handled by the delisting branch, not the band."""
    assert derive_special_treatment_status(name) == DELISTING_STATUS


def test_st_and_delisting_together_prefers_delisting() -> None:
    """A security that is both ST and in delisting must not take the ST band."""
    assert derive_special_treatment_status("*ST海王退") == DELISTING_STATUS


def test_missing_name_has_no_status() -> None:
    """A missing name is not evidence of a normal security."""
    assert derive_special_treatment_status(None) is None
    assert derive_special_treatment_status("") is None
    assert derive_special_treatment_status("   ") is None


def test_marker_must_be_a_leading_token() -> None:
    """A marker in the middle of a name is not a treatment marker.

    Some vendors prefix a short name with a sector or venue tag; the marker
    still has to lead the name's first token.
    """
    assert derive_special_treatment_status("ST某某") == ST_STATUS
    # ``东方ST`` would be a company whose own name contains the letters, not
    # a treatment marker.
    assert derive_special_treatment_status("东方ST科技") == NORMAL_STATUS


def test_is_special_treatment_helper() -> None:
    assert is_special_treatment("*ST美丽") is True
    assert is_special_treatment("平安银行") is False
    assert is_special_treatment("国华退") is False
