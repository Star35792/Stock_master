from datetime import date
from decimal import Decimal

from app.services import FactorPoolClassifierService, FactorPoolPolicy
from app.services.factor_batch_profiler import FactorProfileSummary


def _summary(
    *,
    trade_date_count: int = 250,
    coverage: Decimal | None = Decimal("0.90"),
    rank_ic_1d: Decimal | None = Decimal("0.02"),
    rank_ic_5d: Decimal | None = Decimal("0.02"),
    rank_ic_20d: Decimal | None = Decimal("0.02"),
    long_short_1d: Decimal | None = Decimal("0.002"),
    endpoint_observation_count: int = 250,
    pit_status: str = "point_in_time",
    factor_status: str = "active",
) -> FactorProfileSummary:
    return FactorProfileSummary(
        factor_name="ret_1d",
        factor_version="v1-qfq",
        category="price_volume_risk",
        trade_date_count=trade_date_count,
        first_trade_date=date(2024, 1, 2),
        last_trade_date=date(2025, 12, 31),
        mean_coverage_rate=coverage,
        mean_ic_1d=Decimal("0.01"),
        mean_rank_ic_1d=rank_ic_1d,
        mean_ic_5d=Decimal("0.015"),
        mean_rank_ic_5d=rank_ic_5d,
        mean_ic_20d=Decimal("0.005"),
        mean_rank_ic_20d=rank_ic_20d,
        mean_long_short_forward_return_1d=long_short_1d,
        endpoint_observation_count=endpoint_observation_count,
        pit_status=pit_status,
        factor_status=factor_status,
    )


def test_core_pool_when_all_thresholds_met() -> None:
    service = FactorPoolClassifierService()
    result = service.classify([_summary()])[0]
    assert result.pool == "core"
    assert result.reasons == []


def test_observation_pool_when_partial_ic() -> None:
    service = FactorPoolClassifierService()
    summary = _summary(rank_ic_20d=Decimal("0.005"))
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("rank_ic_20d" in reason for reason in result.reasons)


def test_frozen_pool_when_no_ic() -> None:
    service = FactorPoolClassifierService()
    summary = _summary(
        rank_ic_1d=Decimal("-0.01"),
        rank_ic_5d=Decimal("-0.005"),
        rank_ic_20d=Decimal("0.005"),
    )
    result = service.classify([summary])[0]
    assert result.pool == "frozen"


def test_frozen_pool_when_low_coverage() -> None:
    service = FactorPoolClassifierService()
    summary = _summary(coverage=Decimal("0.50"))
    result = service.classify([summary])[0]
    assert result.pool == "frozen"
    assert any("coverage_rate" in reason for reason in result.reasons)


def test_frozen_pool_when_insufficient_dates() -> None:
    service = FactorPoolClassifierService()
    summary = _summary(trade_date_count=50)
    result = service.classify([summary])[0]
    assert result.pool == "frozen"
    assert any("trade_date_count" in reason for reason in result.reasons)


def test_policy_conservative_raises_bar() -> None:
    service = FactorPoolClassifierService(policy=FactorPoolPolicy.conservative())
    # Conservative policy requires 0.02 on all horizons; 0.015 fails all.
    summary = _summary(
        rank_ic_1d=Decimal("0.015"),
        rank_ic_5d=Decimal("0.015"),
        rank_ic_20d=Decimal("0.015"),
    )
    result = service.classify([summary])[0]
    assert result.pool == "frozen"


def test_classify_preserves_identity_fields() -> None:
    service = FactorPoolClassifierService()
    summary = _summary()
    result = service.classify([summary])[0]
    assert result.factor_name == "ret_1d"
    assert result.factor_version == "v1-qfq"
    assert result.category == "price_volume_risk"
    assert result.summary is summary


# --- endpoint gate ------------------------------------------------------


def test_negative_endpoint_keeps_factor_out_of_core() -> None:
    """A positive IC with a negative endpoint spread is not tradable."""
    service = FactorPoolClassifierService()
    summary = _summary(long_short_1d=Decimal("-0.0005"))
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("long_short_forward_return_1d" in reason for reason in result.reasons)


def test_zero_endpoint_keeps_factor_out_of_core() -> None:
    """The endpoint gate is strict: a zero spread has no tradable edge."""
    service = FactorPoolClassifierService()
    summary = _summary(long_short_1d=Decimal("0"))
    result = service.classify([summary])[0]
    assert result.pool == "observation"


def test_missing_endpoint_evidence_keeps_factor_out_of_core() -> None:
    """No endpoint measurement means no tradability verdict."""
    service = FactorPoolClassifierService()
    summary = _summary(long_short_1d=None, endpoint_observation_count=0)
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("端点" in reason for reason in result.reasons)


def test_endpoint_threshold_is_configurable() -> None:
    policy = FactorPoolPolicy(min_long_short_forward_return_1d=Decimal("0.001"))
    service = FactorPoolClassifierService(policy=policy)
    summary = _summary(long_short_1d=Decimal("0.0005"))
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    summary = _summary(long_short_1d=Decimal("0.0015"))
    result = service.classify([summary])[0]
    assert result.pool == "core"


# --- point-in-time gate -------------------------------------------------


def test_look_ahead_factor_is_frozen() -> None:
    """A factor with a look-ahead input must not enter any pool."""
    service = FactorPoolClassifierService()
    summary = _summary(pit_status="look_ahead")
    result = service.classify([summary])[0]
    assert result.pool == "frozen"
    assert any("look_ahead" in reason for reason in result.reasons)


def test_unknown_pit_status_cannot_enter_core() -> None:
    """Fail-closed: an unaudited factor stays out of the core pool."""
    service = FactorPoolClassifierService()
    summary = _summary(pit_status="unknown")
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("pit_status=unknown" in reason for reason in result.reasons)


def test_default_pit_status_is_unknown() -> None:
    """A summary built without an explicit verdict must fail closed."""
    service = FactorPoolClassifierService()
    summary = FactorProfileSummary(
        factor_name="ret_1d",
        factor_version="v1-qfq",
        category="price_volume_risk",
        trade_date_count=250,
        first_trade_date=date(2024, 1, 2),
        last_trade_date=date(2025, 12, 31),
        mean_coverage_rate=Decimal("0.90"),
        mean_rank_ic_1d=Decimal("0.02"),
        mean_rank_ic_5d=Decimal("0.02"),
        mean_rank_ic_20d=Decimal("0.02"),
        mean_long_short_forward_return_1d=Decimal("0.002"),
        endpoint_observation_count=250,
    )
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("pit_status=unknown" in reason for reason in result.reasons)


# --- lifecycle gate -----------------------------------------------------


def test_retired_definition_is_frozen() -> None:
    """A retired definition keeps its evidence but leaves every pool."""
    service = FactorPoolClassifierService()
    summary = _summary(factor_status="retired")
    result = service.classify([summary])[0]
    assert result.pool == "frozen"
    assert any("retired" in reason for reason in result.reasons)


def test_observation_branch_keeps_structural_reasons() -> None:
    """The observation branch must not drop the structural reasons.

    The previous implementation filtered the reason list down to the entries
    containing ``rank_ic``, which silently discarded the coverage, history,
    endpoint and PIT reasons.
    """
    service = FactorPoolClassifierService()
    summary = _summary(
        rank_ic_20d=Decimal("0.005"),
        long_short_1d=Decimal("-0.001"),
        pit_status="unknown",
    )
    result = service.classify([summary])[0]
    assert result.pool == "observation"
    assert any("rank_ic_20d" in reason for reason in result.reasons)
    assert any("long_short_forward_return_1d" in reason for reason in result.reasons)
    assert any("pit_status=unknown" in reason for reason in result.reasons)
