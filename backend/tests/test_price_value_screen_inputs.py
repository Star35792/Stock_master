from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.services.price_value_diagnostics import diagnose
from app.services.price_value_screen_inputs import (
    input_hash,
    prepare_inputs,
    prepare_shortlist_liquidity,
)

SYMBOL = "600001.SH"
DAYS = [date(2026, 10, 1) + timedelta(days=i) for i in range(21)]


def _inputs():
    return {
        "sessions": {"SH": DAYS}, "financial_rows": [],
        "prices": [{
            "id": i + 1, "symbol": SYMBOL, "trade_date": day, "adjust_type": "none",
            "close": Decimal(10) if i < 10 else Decimal(9), "quality_status": "trusted",
            "source_name": "price-source", "is_suspect": False, "is_suspended": False,
            "updated_at": datetime(2026, 10, 21, tzinfo=UTC),
        } for i, day in enumerate(DAYS)],
        "adjustments": [{
            "id": i + 100, "symbol": SYMBOL, "trade_date": day,
            "factor": Decimal("0.9") if i < 10 else Decimal(1),
            "source_name": "factor-source", "version": "v1", "release_id": "release-1",
            "quality_status": "trusted", "updated_at": datetime(2026, 10, 21, tzinfo=UTC),
        } for i, day in enumerate(DAYS)],
    }


def _prepare(inputs):
    prices, _ = prepare_inputs(inputs, {SYMBOL: "SH"})
    return prices[SYMBOL]


def test_shortlist_liquidity_requires_exact_trusted_last_twenty_unadjusted_sessions():
    inputs = _inputs()
    for row in inputs["prices"]:
        row["amount"] = Decimal("50000000.123456789")
    evidence = prepare_shortlist_liquidity(inputs, {SYMBOL: "SH"})[SYMBOL]
    assert evidence["amount_mean_20"] == "50000000.123456789"
    assert len(evidence["source_refs"]) == 20
    assert all(SYMBOL in reference for reference in evidence["source_refs"])
    # First of 21 prices is not part of the last-20 liquidity window.
    inputs["prices"][0]["amount"] = None
    assert prepare_shortlist_liquidity(inputs, {SYMBOL: "SH"})[SYMBOL] == evidence
    for field, value in (("amount", None), ("amount", Decimal("NaN")),
                         ("amount", 50000000.0), ("is_suspended", True),
                         ("is_suspect", True), ("source_name", "other")):
        invalid = deepcopy(inputs)
        invalid["prices"][-1][field] = value
        assert prepare_shortlist_liquidity(invalid, {SYMBOL: "SH"})[SYMBOL][
            "amount_mean_20"
        ] is None
    inputs["prices"].pop()
    assert prepare_shortlist_liquidity(inputs, {SYMBOL: "SH"})[SYMBOL][
        "amount_mean_20"
    ] is None


def test_liquidity_refs_remain_unique_across_symbols_without_legacy_row_ids():
    inputs = _inputs()
    other_symbol = "600003.SH"
    for row in inputs["prices"]:
        row.update(id=None, amount=Decimal("50000000"))
    inputs["prices"] += [{**row, "symbol": other_symbol} for row in inputs["prices"]]
    result = prepare_shortlist_liquidity(inputs, {SYMBOL: "SH", other_symbol: "SH"})
    assert len(set(result[SYMBOL]["source_refs"] + result[other_symbol]["source_refs"])) == 40


def test_ex_dividend_jump_is_flat_after_trusted_factor_reconstruction():
    prices = _prepare(_inputs())
    assert {row["close"] for row in prices} == {Decimal(9)}
    result = diagnose(symbol=SYMBOL, as_of=DAYS[-1], sessions=DAYS, prices=prices,
                      financial_rows=[])
    assert result["trend"]["status"] == "mixed"
    assert Decimal(result["trend"]["return_20"]) == 0
    assert Decimal(result["trend"]["ma20_gap"]) == 0
    assert not result["review_eligible"]


def test_missing_target_factor_does_not_fall_back_to_unadjusted_or_one():
    inputs = _inputs()
    inputs["adjustments"].pop()
    assert _prepare(inputs) == []


def test_incomplete_versions_cannot_be_combined():
    inputs = _inputs()
    inputs["adjustments"][-1]["version"] = "v2"
    assert _prepare(inputs) == []
    inputs = _inputs()
    inputs["adjustments"][-1]["release_id"] = "release-2"
    assert _prepare(inputs) == []


def test_partial_direct_qfq_is_not_patched_with_reconstructed_prices():
    inputs = _inputs()
    inputs["prices"].append({**inputs["prices"][0], "adjust_type": "qfq"})
    assert len(_prepare(inputs)) == 1


def test_direct_qfq_must_have_a_single_source_and_never_mix_anchors():
    inputs = _inputs()
    for row in inputs["prices"]:
        row["adjust_type"] = "qfq"
    inputs["prices"][-1]["source_name"] = "other-anchor"
    assert _prepare(inputs) == []


def test_latest_complete_group_is_deterministic_and_hash_binds_factor_content():
    inputs = _inputs()
    other = deepcopy(inputs["adjustments"])
    for row in other:
        row.update(id=row["id"] + 100, version="v2",
                   updated_at=datetime(2026, 10, 22, tzinfo=UTC))
    inputs["adjustments"] += other
    first = _prepare(inputs)
    assert all(":v2:" in row["source_name"] for row in first)
    before = input_hash(sessions=DAYS, prices=first, financial=[])
    inputs["adjustments"].reverse()
    assert _prepare(inputs) == first
    inputs["adjustments"][0]["factor"] = Decimal("0.99")
    after = input_hash(sessions=DAYS, prices=_prepare(inputs), financial=[])
    assert after != before


def test_invalid_factor_cannot_create_a_complete_group():
    for value in (Decimal(0), Decimal("NaN"), Decimal("Infinity")):
        inputs = _inputs()
        inputs["adjustments"][0]["factor"] = value
        assert _prepare(inputs) == []
