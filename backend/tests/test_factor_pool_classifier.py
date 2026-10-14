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
