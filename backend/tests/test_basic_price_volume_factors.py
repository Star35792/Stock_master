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
        amount=(Decimal(volume) * Decimal(close)) if volume is not None else None,
        turnover_rate=Decimal(turnover_rate) if turnover_rate is not None else None,
    )


def _symbol_bar(symbol: str, trade_date: date, close: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
        close=Decimal(close),
        pre_close=None,
        volume=Decimal("1000"),
        amount=Decimal("1000") * Decimal(close),
        turnover_rate=Decimal("0.01"),
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
        factor_names=[
            "ret_1d",
            "volatility_5",
            "momentum_5",
            "ma_gap_5",
            "volume_chg_1d",
            "turnover_rate",
            "turnover_percentile_120",
            "abnormal_volume_pressure",
        ],
    )

    assert result.rows_by_factor == {
        "ret_1d": 5,
        "volatility_5": 1,
        "momentum_5": 1,
        "ma_gap_5": 2,
        "volume_chg_1d": 5,
        "turnover_rate": 6,
        "turnover_percentile_120": 0,
        "abnormal_volume_pressure": 0,
    }
    assert result.total_rows == 20
    assert len(definition_repository.definitions) == 8
    assert all(
        definition.applicable_security_types == ["stock", "etf", "index"]
        for definition in definition_repository.definitions
    )
    assert len(run_repository.finished) == 8
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
        factor_names=[
            "ret_1d",
            "volatility_5",
            "momentum_5",
            "ma_gap_5",
            "volume_chg_1d",
            "turnover_rate",
            "turnover_percentile_120",
            "abnormal_volume_pressure",
        ],
    )

    assert result.rows_by_factor == {
        "ret_1d": 1,
        "volatility_5": 0,
        "momentum_5": 0,
        "ma_gap_5": 0,
        "volume_chg_1d": 0,
        "turnover_rate": 0,
        "turnover_percentile_120": 0,
        "abnormal_volume_pressure": 0,
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


def test_optimized_default_factors_cover_multiple_horizons() -> None:
    service = BasicPriceVolumeFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=FakeFactorValueRepository(),
    )
    start = date(2026, 1, 1)
    bars = [
        _bar(
            start + timedelta(days=index),
            str(100 + index),
            None,
            str(1000 + index * 10),
            None,
        )
        for index in range(61)
    ]

    specs = service._select_specs(None)
    values = {spec.name: service._calculate_value(spec.name, bars, 60) for spec in specs}

    assert set(values) == {
        "momentum_20",
        "momentum_60",
        "volatility_20",
        "downside_volatility_20",
        "max_drawdown_20",
        "liquidity_20",
        "volume_stability_20",
    }
    assert values["momentum_20"] == Decimal("0.142857142857142857142857143")
    assert values["momentum_60"] == Decimal("0.6")
    assert values["max_drawdown_20"] == Decimal("0")
    assert values["liquidity_20"] is not None


def test_basic_factor_service_calculates_reversal_beta_and_residual_risk() -> None:
    start = date(2026, 1, 1)
    bars = []
    for index in range(61):
        trade_date = start + timedelta(days=index)
        bars.extend(
            [
                _symbol_bar("AAA", trade_date, str(100 + index)),
                _symbol_bar("BBB", trade_date, str(100 + index * 2)),
            ]
        )
    value_repository = FakeFactorValueRepository()
    service = BasicPriceVolumeFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=start + timedelta(days=60),
        end_date=start + timedelta(days=60),
        factor_names=["reversal_5", "beta_60", "residual_volatility_60"],
    )

    assert result.rows_by_factor == {
        "reversal_5": 2,
        "beta_60": 2,
        "residual_volatility_60": 2,
    }
    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("reversal_5", "AAA")] == Decimal("-0.0322580645")
    assert values[("beta_60", "AAA")] > Decimal("0")
    assert values[("beta_60", "BBB")] > values[("beta_60", "AAA")]
    assert values[("residual_volatility_60", "AAA")] >= Decimal("0")
