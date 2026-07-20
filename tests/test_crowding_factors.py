from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import CrowdingFactorService


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


class FakeFactorValueRepository:
    def __init__(self, rows_by_key: dict[tuple[str, str], list[SimpleNamespace]]) -> None:
        self.rows_by_key = rows_by_key
        self.queries: list[dict] = []
        self.values: list[dict] = []

    def list_for_cross_section(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.queries.append(kwargs)
        key = (str(kwargs["factor_name"]), str(kwargs["factor_version"]))
        return self.rows_by_key.get(key, [])

    def upsert_value(self, db: object, **kwargs: object) -> None:
        self.values.append(kwargs)


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


def _bar(symbol: str, trade_date: date) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
    )


def _value(
    symbol: str,
    trade_date: date,
    *,
    factor_name: str,
    factor_version: str,
    value: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=trade_date,
        factor_name=factor_name,
        factor_version=factor_version,
        value=Decimal(value),
    )


def test_crowding_factor_service_blends_price_pressure_and_sentiment() -> None:
    start_date = date(2026, 7, 15)
    factor_version = "v1-qfq"
    target_dates = [start_date + timedelta(days=index) for index in range(4)]
    history_dates = [start_date - timedelta(days=2), start_date - timedelta(days=1)]

    bars = [
        _bar(symbol, trade_date)
        for trade_date in target_dates
        for symbol in ("A", "B", "C")
    ]
    turnover_rows = [
        _value(
            symbol,
            trade_date,
            factor_name="turnover_percentile_120",
            factor_version=factor_version,
            value=value,
        )
        for trade_date, values in {
            target_dates[0]: {"A": "0.1", "B": "0.2", "C": "0.3"},
            target_dates[1]: {"A": "0.6", "B": "0.4", "C": "0.5"},
            target_dates[2]: {"A": "0.2", "B": "0.8", "C": "0.4"},
            target_dates[3]: {"A": "0.9", "B": "0.1", "C": "0.5"},
        }.items()
        for symbol, value in values.items()
    ]
    abnormal_rows = [
        _value(
            symbol,
            trade_date,
            factor_name="abnormal_volume_pressure",
            factor_version=factor_version,
            value=value,
        )
        for trade_date, values in {
            target_dates[0]: {"A": "1", "B": "2", "C": "3"},
            target_dates[1]: {"A": "3", "B": "2", "C": "1"},
            target_dates[2]: {"A": "2", "B": "1", "C": "3"},
            target_dates[3]: {"A": "4", "B": "0", "C": "2"},
        }.items()
        for symbol, value in values.items()
    ]
    sentiment_rows = [
        _value(
            symbol,
            trade_date,
            factor_name="market_sentiment_score",
            factor_version="v1",
            value=value,
        )
        for trade_date, value in {
            history_dates[0]: "0.1",
            history_dates[1]: "0.9",
            target_dates[0]: "0.2",
            target_dates[1]: "0.8",
            target_dates[2]: "0.3",
            target_dates[3]: "0.7",
        }.items()
        for symbol in ("A", "B", "C")
    ]

    value_repository = FakeFactorValueRepository(
        {
            ("turnover_percentile_120", factor_version): turnover_rows,
            ("abnormal_volume_pressure", factor_version): abnormal_rows,
            ("market_sentiment_score", "v1"): sentiment_rows,
        }
    )
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    daily_bar_repository = FakeDailyBarRepository(bars)
    service = CrowdingFactorService(
        daily_bar_repository=daily_bar_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=start_date,
        end_date=target_dates[-1],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {"crowding_score": 12}
    assert result.total_rows == 12
    assert result.input_rows == (
        len(bars) + len(turnover_rows) + len(abnormal_rows) + len(sentiment_rows)
    )
    assert len(definition_repository.definitions) == 1
    assert definition_repository.definitions[0].name == "crowding_score"
    assert definition_repository.definitions[0].inputs == [
        "factor.turnover_percentile_120:v1-qfq",
        "factor.abnormal_volume_pressure:v1-qfq",
        "factor.market_sentiment_score:v1",
    ]
    assert all(run["status"] == "success" for run in run_repository.finished)
    assert daily_bar_repository.kwargs is not None
    assert daily_bar_repository.kwargs["adjust_type"] == "qfq"

    values = {
        (item["trade_date"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[(target_dates[0], "A")] == Decimal("0.1666666667")
    assert values[(target_dates[0], "B")] == Decimal("0.5000000000")
    assert values[(target_dates[0], "C")] == Decimal("0.8333333333")
    assert values[(target_dates[1], "A")] == Decimal("0.8888888889")
    assert values[(target_dates[1], "B")] == Decimal("0.3888888889")
    assert values[(target_dates[3], "A")] == Decimal("0.8666666667")
    assert values[(target_dates[3], "B")] == Decimal("0.2000000000")


def test_crowding_factor_service_validates_inputs() -> None:
    service = CrowdingFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=FakeFactorValueRepository({}),
    )

    with pytest.raises(ValueError, match="start_date"):
        service.run(
            object(),
            start_date=date(2026, 7, 2),
            end_date=date(2026, 7, 1),
        )
