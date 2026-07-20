from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import SentimentFactorService


class FakeDailyBarRepository:
    def __init__(self, bars: list[SimpleNamespace]) -> None:
        self.bars = bars
        self.factor_kwargs: dict | None = None
        self.count_kwargs: dict | None = None

    def list_for_factor_calculation(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.factor_kwargs = kwargs
        return self.bars

    def count_trusted_by_date(self, db: object, **kwargs: object) -> dict[date, int]:
        self.count_kwargs = kwargs
        return {date(2026, 7, 18): 3}


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


def _bar(symbol: str, trade_date: date = date(2026, 7, 18)) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
    )


def _snapshot(
    dataset: str,
    symbol: str,
    snapshot_date: date,
    *,
    rank: int | None = None,
    value: str | None = None,
    attributes: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        dataset=dataset,
        snapshot_date=snapshot_date,
        item_key=symbol,
        symbol=symbol,
        rank=rank,
        value=Decimal(value) if value is not None else None,
        attributes=attributes or {},
    )


def test_sentiment_factor_service_calculates_snapshot_factors() -> None:
    snapshots = [
        _snapshot("hot_stock", "AAA", date(2026, 7, 17), rank=2, value="100"),
        _snapshot("hot_stock", "BBB", date(2026, 7, 17), rank=1, value="200"),
        _snapshot("hot_stock", "CCC", date(2026, 7, 18), rank=1, value="900"),
        _snapshot("limit_up_pool", "BBB", date(2026, 7, 17), value="300"),
        _snapshot("limit_up_pool", "CCC", date(2026, 7, 17), value="500"),
        _snapshot("skyrocket", "AAA", date(2026, 7, 17), rank=1),
        _snapshot("skyrocket", "CCC", date(2026, 7, 17), rank=2),
        _snapshot(
            "dragon_tiger",
            "AAA",
            date(2026, 7, 17),
            value="12",
            attributes={"net_rate": "0.12"},
        ),
        _snapshot(
            "dragon_tiger_hot_money",
            "BBB",
            date(2026, 7, 17),
            value="20",
            attributes={"net_value": "20", "buy_value": "100", "sell_value": "100"},
        ),
    ]
    daily_bar_repository = FakeDailyBarRepository(
        [_bar("AAA"), _bar("BBB"), _bar("CCC")]
    )
    snapshot_repository = FakeAlternativeDataSnapshotRepository(snapshots)
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = SentimentFactorService(
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
        "hot_rank_score": 2,
        "hot_heat": 2,
        "limit_up_strength": 2,
        "dragon_tiger_net_buy_ratio": 2,
        "limit_up_breadth": 3,
        "market_sentiment_score": 3,
    }
    assert result.total_rows == 14
    assert result.input_rows == len(snapshots)
    assert len(definition_repository.definitions) == 6
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.factor_kwargs is not None
    assert daily_bar_repository.factor_kwargs["symbols"] == ["AAA"]
    assert snapshot_repository.kwargs is not None
    assert snapshot_repository.kwargs["start_date"] == date(2026, 7, 4)

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("hot_rank_score", "AAA")] == Decimal("0.5000000000")
    assert values[("hot_rank_score", "BBB")] == Decimal("1.0000000000")
    assert ("hot_rank_score", "CCC") not in values
    assert values[("hot_heat", "BBB")] == Decimal("200.0000000000")
    assert values[("limit_up_strength", "CCC")] == Decimal("500.0000000000")
    assert values[("dragon_tiger_net_buy_ratio", "AAA")] == Decimal("0.1200000000")
    assert values[("dragon_tiger_net_buy_ratio", "BBB")] == Decimal("0.1000000000")
    assert values[("limit_up_breadth", "AAA")] == Decimal("0.6666666667")
    assert values[("market_sentiment_score", "CCC")] == Decimal("2.0000000000")
    assert all(item["factor_version"] == "v1" for item in value_repository.values)


def test_sentiment_factor_service_validates_inputs() -> None:
    service = SentimentFactorService(
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
