from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import IndustryFactorService


class FakeDailyBarRepository:
    def __init__(self, bars: list[SimpleNamespace]) -> None:
        self.bars = bars
        self.kwargs: dict | None = None

    def list_for_factor_calculation(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.bars


class FakeSecurityRepository:
    def __init__(self, industries: dict[str, str]) -> None:
        self.industries = industries

    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {symbol: self.industries[symbol] for symbol in symbols if symbol in self.industries}


class FakeSecurityIndustryHistoryRepository:
    def __init__(self, by_date: dict[date, dict[str, str]] | None = None) -> None:
        self.by_date = by_date or {}
        self.calls: list[dict] = []

    def industries_as_of(
        self, db: object, *, symbols: set[str] | list[str], as_of: date
    ) -> dict[str, str]:
        self.calls.append({"symbols": set(symbols), "as_of": as_of})
        resolved = self.by_date.get(as_of, {})
        return {s: v for s, v in resolved.items() if s in set(symbols)}


class FakeFactorDefinitionRepository:
    def __init__(self) -> None:
        self.definitions: list[SimpleNamespace] = []

    def ensure(self, db: object, **kwargs: object) -> SimpleNamespace:
        definition = SimpleNamespace(id=len(self.definitions) + 1, **kwargs)
        self.definitions.append(definition)
        return definition


class FakeFactorRunRepository:
    def __init__(self) -> None:
        self.runs: list[SimpleNamespace] = []
        self.finished: list[dict] = []

    def create(self, db: object, **kwargs: object) -> SimpleNamespace:
        run = SimpleNamespace(id=len(self.runs) + 1, **kwargs)
        self.runs.append(run)
        return run

    def update_status(self, db: object, **kwargs: object) -> SimpleNamespace:
        self.finished.append(kwargs)
        return SimpleNamespace(**kwargs)


class FakeFactorValueRepository:
    def __init__(self) -> None:
        self.values: list[dict] = []

    def upsert_value(self, db: object, **kwargs: object) -> None:
        self.values.append(kwargs)


def _bar(
    symbol: str,
    trade_date: date,
    close: str,
    amount: str = "100",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
        close=Decimal(close),
        amount=Decimal(amount),
    )


def test_industry_factor_service_calculates_momentum_and_breadth() -> None:
    bars = []
    for day in range(0, 121):
        trade_date = date.fromordinal(date(2026, 3, 1).toordinal() + day)
        bars.extend(
            [
                _bar("A", trade_date, str(100 + day)),
                _bar("B", trade_date, str(200 - day), str(100 + day)),
                _bar("C", trade_date, str(50 + day * 2), str(300 - day)),
            ]
        )

    value_repository = FakeFactorValueRepository()
    service = IndustryFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        security_repository=FakeSecurityRepository({"A": "Tech", "B": "Tech", "C": "Bank"}),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 5, 25),
        end_date=date(2026, 5, 25),
        symbols=["a", "b", "c"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {
        "industry_momentum_60": 108,
        "industry_breadth_20": 108,
        "industry_turnover_share_change_20": 108,
    }
    values = {
        (item["factor_name"], item["symbol"]): item["value"] for item in value_repository.values
    }
    assert values[("industry_momentum_60", "A")] == values[("industry_momentum_60", "B")]
    assert values[("industry_momentum_60", "A")] != values[("industry_momentum_60", "C")]
    assert values[("industry_momentum_60", "C")] > Decimal("0")
    assert values[("industry_breadth_20", "A")] == Decimal("0.5000000000")
    assert values[("industry_breadth_20", "B")] == Decimal("0.5000000000")
    assert values[("industry_turnover_share_change_20", "A")] > Decimal("0")
    assert (
        values[("industry_turnover_share_change_20", "A")]
        == values[("industry_turnover_share_change_20", "B")]
    )
    assert values[("industry_turnover_share_change_20", "C")] < Decimal("0")


def test_industry_factor_service_uses_point_in_time_industry_labels() -> None:
    """A symbol reclassified mid-window must switch industry at that date."""

    start = date(2026, 3, 1)
    bars = []
    history: dict[date, dict[str, str]] = {}
    cutoff = date.fromordinal(start.toordinal() + 80)
    for day in range(0, 121):
        trade_date = date.fromordinal(start.toordinal() + day)
        bars.extend(
            [
                _bar("A", trade_date, str(100 + day)),
                _bar("B", trade_date, str(200 - day), str(100 + day)),
                _bar("C", trade_date, str(50 + day * 2), str(300 - day)),
            ]
        )
        history[trade_date] = {"C": "Bank" if trade_date <= cutoff else "Tech"}

    value_repository = FakeFactorValueRepository()
    run_repository = FakeFactorRunRepository()
    service = IndustryFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        # Current label says Bank; history reclassifies C to Tech after cutoff.
        security_repository=FakeSecurityRepository({"A": "Tech", "B": "Tech", "C": "Bank"}),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(history),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=start,
        end_date=date.fromordinal(start.toordinal() + 120),
        symbols=["a", "b", "c"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor["industry_momentum_60"] > 0
    values = {
        (item["factor_name"], item["symbol"], item["trade_date"]): item["value"]
        for item in value_repository.values
    }
    before = date.fromordinal(cutoff.toordinal() - 1)
    after = date.fromordinal(cutoff.toordinal() + 1)
    # Before the reclassification C sits alone in Bank (own industry metric).
    assert (
        values[("industry_momentum_60", "C", before)]
        != values[("industry_momentum_60", "A", before)]
    )
    assert values[("industry_breadth_20", "C", before)] == Decimal("1.0000000000")
    # After the reclassification C joins Tech and shares A/B's metric.
    assert (
        values[("industry_momentum_60", "C", after)] == values[("industry_momentum_60", "A", after)]
    )
    assert values[("industry_breadth_20", "C", after)] == Decimal("0.6666666667")
    for run in run_repository.runs:
        assert run.parameters["industry_mapping_source"] == (
            "security_industry_history_as_of_trade_date"
        )
        # A and B have no history rows and fall back to their current labels;
        # C is resolved from point-in-time history.
        classification = run.metadata["industry_classification"]
        assert classification["policy"] == "pit_history_with_current_label_fallback"
        assert classification["fallback_symbols"] == ["A", "B"]


def test_industry_factor_service_validates_inputs() -> None:
    service = IndustryFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        security_repository=FakeSecurityRepository({}),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=FakeFactorValueRepository(),
    )

    with pytest.raises(ValueError, match="start_date"):
        service.run(
            object(),
            start_date=date(2026, 7, 2),
            end_date=date(2026, 7, 1),
        )

    with pytest.raises(ValueError, match="unknown factor names"):
        service.run(
            object(),
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 2),
            factor_names=["not_exists"],
        )
