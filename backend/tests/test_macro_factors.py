from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import MacroFactorService


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


def _snapshot(item_key: str, snapshot_date: date, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        dataset="macro_indicator",
        snapshot_date=snapshot_date,
        item_key=item_key,
        value=Decimal(value),
    )


def test_macro_factor_service_calculates_market_wide_state_factors() -> None:
    snapshots = []
    start_ordinal = date(2026, 6, 27).toordinal()
    for offset in range(21):
        snapshot_date = date.fromordinal(start_ordinal + offset)
        snapshots.extend(
            [
                _snapshot("cn_10y_yield", snapshot_date, str(Decimal("2.30") + offset)),
                _snapshot(
                    "usd_cnh",
                    snapshot_date,
                    str(Decimal("7.00") + Decimal(offset) / Decimal(100)),
                ),
                _snapshot("commodity_proxy", snapshot_date, str(Decimal("100") + offset)),
                _snapshot(
                    "credit_spread",
                    snapshot_date,
                    str(Decimal("0.80") + Decimal(offset) / Decimal(100)),
                ),
            ]
        )
    snapshots.extend(
        [
            _snapshot("cn_1y_yield", date(2026, 7, 17), "1.80"),
            _snapshot("social_financing_yoy", date(2026, 6, 30), "8.10"),
            _snapshot("social_financing_yoy", date(2026, 7, 15), "8.50"),
            _snapshot("m1_yoy", date(2026, 7, 17), "5.20"),
            _snapshot("m2_yoy", date(2026, 7, 17), "7.10"),
        ]
    )

    daily_bar_repository = FakeDailyBarRepository([_bar("AAA"), _bar("BBB")])
    snapshot_repository = FakeAlternativeDataSnapshotRepository(snapshots)
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = MacroFactorService(
        daily_bar_repository=daily_bar_repository,
        snapshot_repository=snapshot_repository,
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
        "rate_trend_10y": 2,
        "yield_curve_slope": 2,
        "social_financing_impulse": 2,
        "m1_m2_gap": 2,
        "usd_cnh_trend": 2,
        "commodity_inflation_proxy": 2,
        "credit_spread_change": 2,
    }
    assert result.total_rows == 14
    assert len(definition_repository.definitions) == 7
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["AAA"]
    assert snapshot_repository.kwargs is not None
    assert snapshot_repository.kwargs["datasets"] == ["macro_indicator"]

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("rate_trend_10y", "AAA")] == Decimal("20.0000000000")
    assert values[("yield_curve_slope", "AAA")] == Decimal("20.5000000000")
    assert values[("social_financing_impulse", "AAA")] == Decimal("0.4000000000")
    assert values[("m1_m2_gap", "AAA")] == Decimal("-1.9000000000")
    assert values[("usd_cnh_trend", "AAA")] == Decimal("0.0285714286")
    assert values[("commodity_inflation_proxy", "AAA")] == Decimal("0.2000000000")
    assert values[("credit_spread_change", "AAA")] == Decimal("0.2000000000")
    assert values[("m1_m2_gap", "BBB")] == Decimal("-1.9000000000")
    assert all(item["factor_version"] == "v1" for item in value_repository.values)


def test_macro_factor_service_validates_inputs() -> None:
    service = MacroFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        snapshot_repository=FakeAlternativeDataSnapshotRepository([]),
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
