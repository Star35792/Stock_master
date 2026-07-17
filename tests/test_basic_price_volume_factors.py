from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import BasicPriceVolumeFactorService


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
    trade_date: date,
    close: str,
    pre_close: str | None,
    volume: str | None,
    turnover_rate: str | None,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol="600519.SH",
        security_type="stock",
        trade_date=trade_date,
        close=Decimal(close),
        pre_close=Decimal(pre_close) if pre_close is not None else None,
        volume=Decimal(volume) if volume is not None else None,
        turnover_rate=Decimal(turnover_rate) if turnover_rate is not None else None,
    )


def test_basic_factor_service_calculates_and_records_factor_runs() -> None:
    start_date = date(2026, 7, 1)
    closes = ("100", "101", "102", "103", "104", "110")
    bars = [
        _bar(
            start_date + timedelta(days=index),
            close,
            "99" if index == 0 else closes[index - 1],
            str(100 + index * 10),
            str(Decimal(index + 1) / Decimal(100)),
        )
        for index, close in enumerate(closes)
    ]
    daily_bar_repository = FakeDailyBarRepository(bars)
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = BasicPriceVolumeFactorService(
        daily_bar_repository=daily_bar_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=start_date,
        end_date=start_date + timedelta(days=5),
        symbols=["600519.sh"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {
        "ret_1d": 5,
        "volatility_5": 1,
        "momentum_5": 1,
        "ma_gap_5": 2,
        "volume_chg_1d": 5,
        "turnover_rate": 6,
    }
    assert result.total_rows == 20
    assert len(definition_repository.definitions) == 6
    assert all(
        definition.applicable_security_types == ["stock", "etf", "index"]
        for definition in definition_repository.definitions
    )
    assert len(run_repository.finished) == 6
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["symbols"] == ["600519.SH"]
    assert daily_bar_repository.kwargs["quality_status"] == "trusted"

    values = {
        (item["factor_name"], item["trade_date"]): item["value"]
        for item in value_repository.values
    }
    last_date = start_date + timedelta(days=5)
    assert values[("momentum_5", last_date)] == Decimal("0.1000000000")
    assert values[("ma_gap_5", last_date)] == Decimal("0.0576923077")
    assert values[("turnover_rate", last_date)] == Decimal("0.0600000000")
    assert values[("volatility_5", last_date)] > 0
    assert all(item["factor_version"] == "v1-qfq" for item in value_repository.values)


def test_basic_factor_service_skips_missing_fields_and_short_windows() -> None:
    start_date = date(2026, 7, 1)
    bars = [
        _bar(start_date, "100", None, None, None),
        _bar(start_date + timedelta(days=1), "101", "100", None, None),
    ]
    value_repository = FakeFactorValueRepository()
    service = BasicPriceVolumeFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=start_date,
        end_date=start_date + timedelta(days=1),
    )

    assert result.rows_by_factor == {
        "ret_1d": 1,
        "volatility_5": 0,
        "momentum_5": 0,
        "ma_gap_5": 0,
        "volume_chg_1d": 0,
        "turnover_rate": 0,
    }
    assert len(value_repository.values) == 1
    assert value_repository.values[0]["factor_name"] == "ret_1d"


def test_basic_factor_service_validates_inputs() -> None:
    service = BasicPriceVolumeFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
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
