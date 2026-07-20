from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import PolicyEventFactorService


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


class FakeAlternativeDataSnapshotRepository:
    def __init__(self, snapshots: list[SimpleNamespace]) -> None:
        self.snapshots = snapshots
        self.kwargs: dict | None = None

    def list_available(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.snapshots


class FakeSecurityRepository:
    def __init__(self, industries: dict[str, str]) -> None:
        self.industries = industries
        self.symbols: set[str] | None = None

    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        self.symbols = symbols
        return {symbol: self.industries[symbol] for symbol in symbols}


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


def _bar(symbol: str, trade_date: date = date(2026, 7, 18)) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
    )


def _snapshot(
    industry: str,
    value: str,
    *,
    snapshot_date: date = date(2026, 7, 17),
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset="policy_event",
        snapshot_date=snapshot_date,
        item_key=industry,
        symbol=None,
        value=Decimal(value),
        attributes={},
    )


def test_policy_event_factor_service_maps_industry_events_to_symbols() -> None:
    daily_bar_repository = FakeDailyBarRepository([_bar("AAA"), _bar("BBB")])
    snapshot_repository = FakeAlternativeDataSnapshotRepository(
        [
            _snapshot("Tech", "0.80"),
            _snapshot("Finance", "-0.30"),
            _snapshot("Tech", "9.99", snapshot_date=date(2026, 7, 18)),
        ]
    )
    security_repository = FakeSecurityRepository({"AAA": "Tech", "BBB": "Finance"})
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = PolicyEventFactorService(
        daily_bar_repository=daily_bar_repository,
        snapshot_repository=snapshot_repository,
        security_repository=security_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 7, 18),
        end_date=date(2026, 7, 18),
        symbols=["aaa"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {"policy_event_score": 2}
    assert result.total_rows == 2
    assert len(definition_repository.definitions) == 1
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["AAA"]
    assert snapshot_repository.kwargs is not None
    assert snapshot_repository.kwargs["datasets"] == ["policy_event"]
    assert security_repository.symbols == {"AAA", "BBB"}

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("policy_event_score", "AAA")] == Decimal("0.8000000000")
    assert values[("policy_event_score", "BBB")] == Decimal("-0.3000000000")


def test_policy_event_factor_service_validates_inputs() -> None:
    service = PolicyEventFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        snapshot_repository=FakeAlternativeDataSnapshotRepository([]),
        security_repository=FakeSecurityRepository({}),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=FakeFactorValueRepository(),
    )

    with pytest.raises(ValueError, match="start_date"):
        service.run(
            object(),
            start_date=date(2026, 7, 19),
            end_date=date(2026, 7, 18),
        )

    with pytest.raises(ValueError, match="unknown factor names"):
        service.run(
            object(),
            start_date=date(2026, 7, 18),
            end_date=date(2026, 7, 18),
            factor_names=["not_exists"],
        )
