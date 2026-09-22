from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.schemas.price_value import PriceValueRequest
from app.services.price_value_research import PriceValueResearchService

VALUATION_AT = datetime(2026, 1, 5, 10, tzinfo=UTC)
NOW = VALUATION_AT + timedelta(days=1)


def research_payload(**changes):
    values = {
        "symbol": "600001.SH",
        "trade_date": "2026-01-05",
        "valuation_at": VALUATION_AT.isoformat(),
        "assumptions_available_at": (VALUATION_AT - timedelta(hours=1)).isoformat(),
        "valid_until": (VALUATION_AT + timedelta(days=3)).isoformat(),
        "assumption_version": "analyst-case-v1",
        "assumptions_note": "Explicit ordinary equity FCFE forecasts in CNY; shares assumed.",
        "currency": "CNY",
        "company_profile": "stable_non_financial",
        "diluted_shares": "10",
        "scenarios": {
            name: {
                "cash_flows": [cash, cash, cash],
                "terminal_cash_flow": cash,
                "cost_of_equity": "0.1",
                "terminal_growth": "0",
            }
            for name, cash in (("low", "10"), ("base", "13"), ("high", "16"))
        },
        "required_margin": "0.3",
        "quantity": 100,
        "fees": {
            "commission_rate": "0",
            "min_commission": "5",
            "transfer_fee_rate": "0",
            "exchange_fee_rate": "0",
            "slippage_bps": "0",
        },
        "has_position": False,
        "thesis_broken": False,
    }
    values.update(changes)
    return PriceValueRequest.model_validate(values)


def trusted_bar(**changes):
    values = dict(
        symbol="600001.SH",
        security_type="stock",
        trade_date=date(2026, 1, 5),
        adjust_type="none",
        close=Decimal("6.50"),
        source_name="fixture-source",
        quality_status="trusted",
        is_suspect=False,
        selected_raw_id=101,
        updated_at=VALUATION_AT - timedelta(minutes=30),
        volume=Decimal("1000000"),
        amount=Decimal("6500000"),
        is_suspended=False,
        special_treatment_status="normal",
        limit_up=Decimal("20"),
        limit_down=Decimal("1"),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def security_record(**changes):
    values = dict(
        symbol="600001.SH",
        security_type="stock",
        exchange="SH",
        currency="CNY",
        list_date=date(2000, 1, 1),
        delist_date=None,
        is_active=True,
        industry="食品饮料",
    )
    values.update(changes)
    return SimpleNamespace(**values)


class BarRepository:
    def __init__(self, bar):
        self.bar = bar
        self.requests = []

    def get_by_key(self, db, **kwargs):
        self.requests.append(kwargs)
        return self.bar


class SecurityRepository:
    def __init__(self, security):
        self.security = security

    def get_by_symbol(self, db, **kwargs):
        return self.security


def service_for(bar=None, security=None):
    return PriceValueResearchService(
        daily_bar_repository=BarRepository(bar if bar is not None else trusted_bar()),
        security_repository=SecurityRepository(
            security if security is not None else security_record()
        ),
    )


def test_read_only_evaluation_uses_exact_unadjusted_market_quote():
    service = service_for()
    result = service.evaluate(object(), research_payload(), now=NOW)
    assert service.daily_bar_repository.requests == [
        {"symbol": "600001.SH", "trade_date": date(2026, 1, 5), "adjust_type": "none"}
    ]
    assert result.status == "price_condition_met"
    assert result.valuation["low"] == Decimal("10")
    assert result.price_metrics.all_in_price == Decimal("6.55")
    assert result.price_metrics.buy_cost_ceiling == Decimal("7")
    assert result.price_metrics.cost_adjusted_margin == Decimal("0.345")
    assert result.research_only and not result.execution_eligible and not result.pit_verified
    assert result.new_allocation == "none"
    assert result.existing_position_action == "unchanged"
    assert {item.code for item in result.limitations} == {
        "assumption_only",
        "pit_unverified",
        "return_not_evaluated",
        "portfolio_not_evaluated",
    }


def test_fees_change_price_decision_and_holding_does_not_force_exit():
    result = service_for(trusted_bar(close=Decimal("7"))).evaluate(
        object(), research_payload(has_position=True), now=NOW
    )
    assert result.status == "wait_for_price"
    assert result.price_metrics.gross_margin == Decimal("0.3")
    assert result.price_metrics.cost_adjusted_margin < Decimal("0.3")
    assert result.existing_position_action == "unchanged"
    assert result.new_allocation == "none"


def test_equality_at_all_in_threshold_is_allowed_only_as_research():
    result = service_for(trusted_bar(close=Decimal("6.95"))).evaluate(
        object(), research_payload(), now=NOW
    )
    assert result.price_metrics.all_in_price == result.price_metrics.buy_cost_ceiling
    assert result.status == "price_condition_met"
    assert not result.execution_eligible


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"quality_status": "suspect"}, "untrusted_quote"),
        ({"is_suspect": True}, "untrusted_quote"),
        ({"close": Decimal("0")}, "invalid_quote"),
        ({"adjust_type": "qfq"}, "quote_identity_mismatch"),
        ({"trade_date": date(2026, 1, 6)}, "quote_identity_mismatch"),
        ({"updated_at": VALUATION_AT + timedelta(seconds=1)}, "quote_revised_after_valuation"),
        ({"updated_at": None}, "quote_time_unknown"),
        ({"is_suspended": True}, "suspended"),
        ({"special_treatment_status": "ST"}, "treatment_restricted_or_unknown"),
        ({"special_treatment_status": None}, "treatment_restricted_or_unknown"),
        ({"volume": None}, "liquidity_missing"),
        ({"amount": Decimal("0")}, "liquidity_missing"),
        ({"limit_up": Decimal("6.5")}, "at_upper_limit"),
        ({"source_name": ""}, "quote_source_missing"),
    ],
)
def test_market_gates_cannot_be_offset_by_low_price(changes, reason):
    result = service_for(trusted_bar(**changes)).evaluate(object(), research_payload(), now=NOW)
    assert result.status == "blocked"
    assert reason in {item.code for item in result.blocking_reasons}
    assert result.price_metrics is None
    assert result.new_allocation == "none"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"security_type": "etf"}, "unsupported_security"),
        ({"currency": "USD"}, "currency_mismatch"),
        ({"is_active": False}, "inactive_security"),
        ({"list_date": None}, "listing_unverified"),
        ({"list_date": date(2026, 1, 6)}, "listing_unverified"),
        ({"delist_date": date(2026, 1, 5)}, "delisted_security"),
        ({"industry": "银行"}, "financial_industry"),
        ({"industry": "Insurance"}, "financial_industry"),
        ({"industry": None}, "industry_missing"),
    ],
)
def test_security_gates(changes, reason):
    result = service_for(security=security_record(**changes)).evaluate(
        object(), research_payload(), now=NOW
    )
    assert result.status == "blocked"
    assert reason in {item.code for item in result.blocking_reasons}
    assert result.price_metrics is None


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"valuation_at": NOW + timedelta(days=1)}, "future_valuation"),
        ({"assumptions_available_at": VALUATION_AT + timedelta(seconds=1)}, "future_assumptions"),
        ({"valid_until": VALUATION_AT - timedelta(seconds=1)}, "expired_assumptions"),
        ({"trade_date": date(2026, 1, 6)}, "quote_not_available"),
        ({"valuation_at": VALUATION_AT.replace(hour=6)}, "quote_not_available"),
        ({"trade_date": date(2025, 12, 20)}, "stale_quote"),
        ({"company_profile": "financial"}, "unsupported_model"),
        ({"company_profile": "cyclical"}, "unsupported_model"),
        ({"company_profile": "distressed"}, "unsupported_model"),
    ],
)
def test_assumption_time_and_model_gates(changes, reason):
    result = service_for().evaluate(object(), research_payload(**changes), now=NOW)
    assert reason in {item.code for item in result.blocking_reasons}
    assert result.price_metrics is None
    assert not result.execution_eligible


def test_missing_data_does_not_fall_back_to_demo_or_latest_quote():
    service = PriceValueResearchService(
        daily_bar_repository=BarRepository(None), security_repository=SecurityRepository(None)
    )
    result = service.evaluate(object(), research_payload(), now=NOW)
    assert {item.code for item in result.blocking_reasons} == {"security_missing", "quote_missing"}
    assert result.market_snapshot is None and result.price_metrics is None


def test_held_stock_at_base_value_requests_review_without_sell_signal():
    result = service_for(trusted_bar(close=Decimal("13"))).evaluate(
        object(), research_payload(has_position=True), now=NOW
    )
    assert result.status == "review_required"
    assert result.existing_position_action == "review"
    assert result.review_reasons[0].code == "at_base_value"
    assert not result.execution_eligible


def test_broken_thesis_blocks_averaging_down_and_reviews_existing_position():
    result = service_for(trusted_bar(close=Decimal("1"))).evaluate(
        object(), research_payload(has_position=True, thesis_broken=True), now=NOW
    )
    assert result.status == "blocked" and result.price_metrics is None
    assert result.existing_position_action == "review"


def test_price_drop_with_larger_value_drop_is_not_a_buy():
    payload = research_payload()
    data = payload.model_dump(mode="json")
    data["scenarios"]["low"]["cash_flows"] = ["6", "6", "6"]
    data["scenarios"]["low"]["terminal_cash_flow"] = "6"
    result = service_for(trusted_bar(close=Decimal("5"))).evaluate(
        object(), PriceValueRequest.model_validate(data), now=NOW
    )
    assert result.price_metrics.buy_cost_ceiling == Decimal("4.2")
    assert result.status == "wait_for_price"


def test_contract_hash_binds_assumptions_market_version_and_restrictions():
    service = service_for()
    first = service.evaluate(object(), research_payload(), now=NOW)
    repeated = service.evaluate(object(), research_payload(), now=NOW + timedelta(seconds=1))
    assert repeated.contract_hash == first.contract_hash
    changed_assumption = service.evaluate(
        object(), research_payload(assumption_version="analyst-case-v2"), now=NOW
    )
    changed_quote = service_for(trusted_bar(close=Decimal("6.4"))).evaluate(
        object(), research_payload(), now=NOW
    )
    changed_restriction = service_for(trusted_bar(is_suspended=True)).evaluate(
        object(), research_payload(), now=NOW
    )
    changed_type = service_for(trusted_bar(security_type="etf")).evaluate(
        object(), research_payload(), now=NOW
    )
    assert changed_type.status == "blocked"
    assert (
        len(
            {
                first.contract_hash,
                changed_assumption.contract_hash,
                changed_quote.contract_hash,
                changed_restriction.contract_hash,
                changed_type.contract_hash,
            }
        )
        == 5
    )
