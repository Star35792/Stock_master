from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import EarningsRevisionFactorService


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

    def upsert_values(self, db: object, rows: list[dict]) -> int:
        self.values.extend(rows)
        return len(rows)


def _bar(symbol: str, trade_date: date = date(2026, 7, 18)) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
    )


def _snapshot(
    dataset: str,
    symbol: str,
    value: str,
    *,
    snapshot_date: date = date(2026, 7, 17),
    attributes: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset=dataset,
        snapshot_date=snapshot_date,
        item_key=symbol,
        symbol=symbol,
        value=Decimal(value),
        attributes=attributes or {},
    )


def test_earnings_revision_factor_service_calculates_forward_factors() -> None:
    snapshots = [
        _snapshot(
            "earnings_revision",
            "AAA",
            "0.10",
            attributes={"forecast_dispersion": "0.20"},
        ),
        _snapshot(
            "earnings_revision",
            "BBB",
            "-0.05",
            attributes={"forecast_dispersion": "0.30"},
        ),
        _snapshot(
            "earnings_revision",
            "CCC",
            "0.20",
            attributes={"forecast_dispersion": "0.15"},
        ),
        _snapshot("profit_guidance", "AAA", "0.15"),
        _snapshot("profit_guidance", "CCC", "-0.10"),
        _snapshot("profit_warning", "BBB", "1"),
        _snapshot("profit_warning", "CCC", "0"),
        _snapshot(
            "earnings_revision",
            "AAA",
            "9.99",
            snapshot_date=date(2026, 7, 18),
            attributes={"forecast_dispersion": "9.99"},
        ),
    ]
    daily_bar_repository = FakeDailyBarRepository(
        [_bar("AAA"), _bar("BBB"), _bar("CCC")]
    )
    snapshot_repository = FakeAlternativeDataSnapshotRepository(snapshots)
    security_repository = FakeSecurityRepository(
        {"AAA": "Tech", "BBB": "Tech", "CCC": "Finance"}
    )
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = EarningsRevisionFactorService(
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

    assert result.rows_by_factor == {
        "earnings_revision_1m": 3,
        "earnings_revision_breadth": 3,
        "forecast_dispersion": 3,
        "guidance_surprise": 2,
        "profit_warning_risk": 2,
    }
    assert result.total_rows == 13
    assert len(definition_repository.definitions) == 5
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["AAA"]
    assert snapshot_repository.kwargs is not None
    assert snapshot_repository.kwargs["datasets"] == [
        "earnings_revision",
        "profit_guidance",
        "profit_warning",
    ]
    assert security_repository.symbols == {"AAA", "BBB", "CCC"}

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("earnings_revision_1m", "AAA")] == Decimal("0.1000000000")
    assert values[("earnings_revision_breadth", "AAA")] == Decimal("0E-10")
    assert values[("earnings_revision_breadth", "CCC")] == Decimal("1.0000000000")
    assert values[("forecast_dispersion", "AAA")] == Decimal("0.2000000000")
    assert values[("guidance_surprise", "AAA")] == Decimal("0.1500000000")
    assert values[("profit_warning_risk", "BBB")] == Decimal("1.0000000000")
    assert ("guidance_surprise", "BBB") not in values


def test_earnings_revision_factor_service_validates_inputs() -> None:
    service = EarningsRevisionFactorService(
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
