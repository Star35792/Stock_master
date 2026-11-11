from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import FactorCrossSectionService


class FakeFactorValueRepository:
    def __init__(
        self,
        source_rows: list[SimpleNamespace],
        size_rows: list[SimpleNamespace],
    ) -> None:
        self.source_rows = source_rows
        self.size_rows = size_rows
        self.values: list[dict] = []

    def list_for_cross_section(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        if kwargs["factor_name"] == "size":
            return self.size_rows
        return self.source_rows

    def upsert_value(self, db: object, **kwargs: object) -> None:
        self.values.append(kwargs)

    def upsert_values(self, db: object, rows: list[dict]) -> int:
        self.values.extend(rows)
        return len(rows)


class FakeProfileRepository:
    def __init__(self) -> None:
        self.profiles: list[dict] = []

    def upsert(self, db: object, **kwargs: object) -> None:
        self.profiles.append(kwargs)


class FakeDefinitionRepository:
    def __init__(self) -> None:
        self.definitions: list[SimpleNamespace] = []

    def ensure(self, db: object, **kwargs: object) -> SimpleNamespace:
        definition = SimpleNamespace(id=len(self.definitions) + 1, **kwargs)
        self.definitions.append(definition)
        return definition

    def get_by_name_version(
        self,
        db: object,
        *,
        name: str,
        version: str,
    ) -> SimpleNamespace | None:
        return None


class FakeRunRepository:
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


class FakeDailyBarRepository:
    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = rows

    def count_trusted_by_date(self, db: object, **kwargs: object) -> dict[date, int]:
        return {date(2026, 7, 17): 5}

    def list_for_factor_calculation(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        if self.rows is not None:
            return self.rows
        rows = []
        symbols = {"A": 1, "B": 2, "C": 3, "D": 4}
        for offset in range(21):
            trade_date = date.fromordinal(date(2026, 7, 17).toordinal() + offset)
            for symbol, slope in symbols.items():
                rows.append(_bar(symbol, trade_date, str(100 + offset * slope)))
        return rows


class FakeSecurityRepository:
    def __init__(self, exchanges: dict[str, str] | None = None) -> None:
        self.exchanges = exchanges or {}

    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {"A": "I1", "B": "I1", "C": "I2", "D": "I2"}

    def get_many(self, db: object, *, symbols: set[str]) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(symbol=symbol, exchange=self.exchanges.get(symbol, "XSHG"))
            for symbol in symbols
        ]


class FakeSecurityIndustryHistoryRepository:
    """时点行业区间替身。

    默认没有任何历史区间 → 服务会走**当前标签回退**，与这些用例原本的
    断言语义一致。构造时传入 intervals 就能构造真实的时点行业场景。
    """

    def __init__(self, intervals: dict[str, list] | None = None) -> None:
        self.intervals = intervals or {}

    def load_intervals(self, db: object, *, symbols: set[str]) -> dict[str, list]:
        return {
            symbol: rows for symbol, rows in self.intervals.items() if symbol in symbols
        }


class FakeTradingCalendarRepository:
    def __init__(
        self,
        open_dates: list[date] | dict[str, list[date]] | None = None,
    ) -> None:
        self.open_dates = open_dates

    def list_open_dates(
        self,
        db: object,
        *,
        exchange: str,
        start_date: date,
        end_date: date,
    ) -> list[date]:
        if isinstance(self.open_dates, dict):
            dates = self.open_dates.get(exchange, [])
        else:
            dates = self.open_dates or [
                date.fromordinal(start_date.toordinal() + offset)
                for offset in range(21)
            ]
        return [item for item in dates if start_date <= item <= end_date]


def _row(symbol: str, factor_name: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=date(2026, 7, 17),
        factor_name=factor_name,
        factor_version="v1-none",
        value=Decimal(value),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


def _bar(symbol: str, trade_date: date, close: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=trade_date,
        close=Decimal(close),
    )


def test_cross_section_service_transforms_and_profiles_factor() -> None:
    source_rows = [
        _row("A", "ret_1d", "1"),
        _row("B", "ret_1d", "2"),
        _row("C", "ret_1d", "3"),
        _row("D", "ret_1d", "100"),
    ]
    size_rows = [
        _row("A", "size", "10"),
        _row("B", "size", "20"),
        _row("C", "size", "10"),
        _row("D", "size", "20"),
    ]
    value_repository = FakeFactorValueRepository(source_rows, size_rows)
    profile_repository = FakeProfileRepository()
    definition_repository = FakeDefinitionRepository()
    run_repository = FakeRunRepository()
    service = FactorCrossSectionService(
        value_repository=value_repository,
        profile_repository=profile_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        daily_bar_repository=FakeDailyBarRepository(),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    result = service.run(
        object(),
        factor_name="ret_1d",
        factor_version="v1-none",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        adjust_type="none",
        code_version="abc123",
        size_factor_name="size",
        size_factor_version="v1-none",
    )

    assert result.profile_rows == 1
    assert result.rows_by_transform == {
        "winsor": 4,
        "zscore": 4,
        "rank": 4,
        "industry_neutral": 4,
        "industry_size_neutral": 4,
    }
    assert result.total_rows == 20
    assert profile_repository.profiles[0]["coverage_rate"] == Decimal("0.8000000000")
    assert profile_repository.profiles[0]["missing_rate"] == Decimal("0.2000000000")
    assert profile_repository.profiles[0]["ic_1d"] > Decimal("0")
    assert profile_repository.profiles[0]["rank_ic_1d"] == Decimal("1.0000000000")
    assert profile_repository.profiles[0]["ic_5d"] > Decimal("0")
    assert profile_repository.profiles[0]["rank_ic_5d"] == Decimal("1.0000000000")
    assert profile_repository.profiles[0]["ic_20d"] > Decimal("0")
    assert profile_repository.profiles[0]["rank_ic_20d"] == Decimal("1.0000000000")
    assert profile_repository.profiles[0]["top_quantile_forward_return_1d"] == Decimal(
        "0.0400000000"
    )
    assert profile_repository.profiles[0]["bottom_quantile_forward_return_1d"] == Decimal(
        "0.0100000000"
    )
    assert profile_repository.profiles[0]["long_short_forward_return_1d"] == Decimal(
        "0.0300000000"
    )
    assert profile_repository.profiles[0]["quantile_monotonicity_1d"] == Decimal(
        "1.0000000000"
    )
    assert profile_repository.profiles[0]["top_quantile_turnover"] is None
    assert len(definition_repository.definitions) == 5
    assert len(run_repository.finished) == 5

    values = {
        (item["factor_name"], item["symbol"]): item["value"]
        for item in value_repository.values
    }
    assert values[("ret_1d__rank", "A")] == Decimal("0E-10")
    assert values[("ret_1d__rank", "D")] == Decimal("1.0000000000")
    assert values[("ret_1d__industry_neutral", "A")] == Decimal("-0.5000000000")
    assert values[("ret_1d__industry_neutral", "B")] == Decimal("0.5000000000")
    assert values[("ret_1d__industry_size_neutral", "A")] == Decimal("24.0000000000")
    assert values[("ret_1d__industry_size_neutral", "B")] == Decimal("-24.0000000000")


def test_write_profiles_skips_days_without_observations() -> None:
    """A day with no observation must not overwrite the existing evidence.

    Writing an empty cross-section would replace the row for the same
    ``(factor_name, factor_version, trade_date)`` key with an
    ``observation_count=0`` placeholder, which is how a batch run over a
    factor whose values are absent silently destroys another script's IC
    evidence.
    """
    profile_repository = FakeProfileRepository()
    service = FactorCrossSectionService(
        value_repository=FakeFactorValueRepository([], []),
        profile_repository=profile_repository,
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        daily_bar_repository=FakeDailyBarRepository(),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    rows_written = service._write_profiles(
        object(),
        factor_name="ret_1d",
        factor_version="v1-none",
        values_by_date={date(2026, 7, 17): {"A": Decimal("1")}},
        universe_by_date={date(2026, 7, 16): 5, date(2026, 7, 17): 5},
        forward_returns_by_horizon={},
        code_version="abc123",
    )

    assert rows_written == 1
    assert len(profile_repository.profiles) == 1
    assert profile_repository.profiles[0]["trade_date"] == date(2026, 7, 17)
    assert profile_repository.profiles[0]["observation_count"] == 1


def test_cross_section_service_validates_size_factor_pair() -> None:
    service = FactorCrossSectionService(
        value_repository=FakeFactorValueRepository([], []),
        profile_repository=FakeProfileRepository(),
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        daily_bar_repository=FakeDailyBarRepository(),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(),
    )

    with pytest.raises(ValueError, match="provided together"):
        service.run(
            object(),
            factor_name="ret_1d",
            factor_version="v1-none",
            start_date=date(2026, 7, 17),
            end_date=date(2026, 7, 17),
            adjust_type="none",
            code_version="abc123",
            size_factor_name="size",
        )


def test_cross_section_forward_return_does_not_skip_missing_target_open_day() -> None:
    source_rows = [
        _row(symbol, "ret_1d", value)
        for symbol, value in {
            "A": "1",
            "B": "2",
        }.items()
    ]
    value_repository = FakeFactorValueRepository(source_rows, [])
    profile_repository = FakeProfileRepository()
    service = FactorCrossSectionService(
        value_repository=value_repository,
        profile_repository=profile_repository,
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        daily_bar_repository=FakeDailyBarRepository(
            [
                _bar("A", date(2026, 7, 17), "100"),
                _bar("A", date(2026, 7, 21), "150"),
                _bar("B", date(2026, 7, 17), "100"),
                _bar("B", date(2026, 7, 21), "50"),
            ]
        ),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(
            [date(2026, 7, 17), date(2026, 7, 20), date(2026, 7, 21)]
        ),
    )

    service.run(
        object(),
        factor_name="ret_1d",
        factor_version="v1-none",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        adjust_type="none",
        code_version="abc123",
    )

    profile = profile_repository.profiles[0]
    assert profile["ic_1d"] is None
    assert profile["rank_ic_1d"] is None
    assert profile["top_quantile_forward_return_1d"] is None
    assert profile["bottom_quantile_forward_return_1d"] is None


def test_cross_section_forward_return_uses_next_exchange_open_day_across_holiday() -> None:
    source_rows = [
        _row("A", "ret_1d", "1"),
        _row("B", "ret_1d", "2"),
        _row("C", "ret_1d", "3"),
        _row("D", "ret_1d", "4"),
    ]
    profile_repository = FakeProfileRepository()
    service = FactorCrossSectionService(
        value_repository=FakeFactorValueRepository(source_rows, []),
        profile_repository=profile_repository,
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        daily_bar_repository=FakeDailyBarRepository(
            [
                *[_bar(symbol, date(2026, 7, 17), "100") for symbol in "ABCD"],
                *[
                    _bar(symbol, target_date, str(100 + slope))
                    for symbol, target_date, slope in (
                        ("A", date(2026, 7, 21), 1),
                        ("B", date(2026, 7, 21), 2),
                        ("C", date(2026, 7, 20), 3),
                        ("D", date(2026, 7, 20), 4),
                    )
                ],
            ]
        ),
        security_repository=FakeSecurityRepository(
            {"A": "XSHG", "B": "XSHG", "C": "XSHE", "D": "XSHE"}
        ),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        trading_calendar_repository=FakeTradingCalendarRepository(
            {
                "XSHG": [date(2026, 7, 17), date(2026, 7, 21)],
                "XSHE": [date(2026, 7, 17), date(2026, 7, 20)],
            }
        ),
    )

    service.run(
        object(),
        factor_name="ret_1d",
        factor_version="v1-none",
        start_date=date(2026, 7, 17),
        end_date=date(2026, 7, 17),
        adjust_type="none",
        code_version="abc123",
    )

    profile = profile_repository.profiles[0]
    assert profile["rank_ic_1d"] == Decimal("1.0000000000")
    assert profile["top_quantile_forward_return_1d"] == Decimal("0.0400000000")
    assert profile["bottom_quantile_forward_return_1d"] == Decimal("0.0100000000")


def test_quantile_diagnostics_include_complete_boundary_tie_groups() -> None:
    service = FactorCrossSectionService()
    factor_values = {
        "AAA": Decimal("1"),
        "BBB": Decimal("2"),
        "CCC": Decimal("2.5"),
        "DDD": Decimal("3"),
        "EEE": Decimal("3"),
    }
    forward_returns = {
        "AAA": Decimal("0.1"),
        "BBB": Decimal("0.2"),
        "CCC": Decimal("0.3"),
        "DDD": Decimal("-0.5"),
        "EEE": Decimal("0.5"),
    }

    diagnostics = service._quantile_forward_diagnostics(
        factor_values,
        forward_returns,
    )

    assert diagnostics.top_return == Decimal("0")
    assert diagnostics.bottom_return == Decimal("0.1")
    assert diagnostics.long_short_return == Decimal("-0.1")
    assert service._top_quantile_symbols(factor_values) == {"DDD", "EEE"}


def test_quantile_diagnostics_are_neutral_without_factor_dispersion() -> None:
    service = FactorCrossSectionService()
    factor_values = {
        "AAA": Decimal("1"),
        "BBB": Decimal("1"),
        "CCC": Decimal("1"),
        "DDD": Decimal("1"),
        "EEE": Decimal("1"),
    }
    forward_returns = {
        "AAA": Decimal("-0.2"),
        "BBB": Decimal("-0.1"),
        "CCC": Decimal("0"),
        "DDD": Decimal("0.1"),
        "EEE": Decimal("0.2"),
    }

    diagnostics = service._quantile_forward_diagnostics(
        factor_values,
        forward_returns,
    )

    assert diagnostics.top_return == Decimal("0")
    assert diagnostics.bottom_return == Decimal("0")
    assert diagnostics.long_short_return == Decimal("0")
    assert diagnostics.monotonicity is None
    assert service._top_quantile_symbols(factor_values) == set(factor_values)


def test_quantile_diagnostics_do_not_depend_on_symbols_inside_ties() -> None:
    service = FactorCrossSectionService()
    baseline = service._quantile_forward_diagnostics(
        {
            "AAA": Decimal("1"),
            "BBB": Decimal("2"),
            "CCC": Decimal("2.5"),
            "DDD": Decimal("3"),
            "EEE": Decimal("3"),
        },
        {
            "AAA": Decimal("0.1"),
            "BBB": Decimal("0.2"),
            "CCC": Decimal("0.3"),
            "DDD": Decimal("-0.5"),
            "EEE": Decimal("0.5"),
        },
    )
    renamed = service._quantile_forward_diagnostics(
        {
            "AAA": Decimal("1"),
            "BBB": Decimal("2"),
            "CCC": Decimal("2.5"),
            "A_TIE": Decimal("3"),
            "Z_TIE": Decimal("3"),
        },
        {
            "AAA": Decimal("0.1"),
            "BBB": Decimal("0.2"),
            "CCC": Decimal("0.3"),
            "A_TIE": Decimal("0.5"),
            "Z_TIE": Decimal("-0.5"),
        },
    )

    assert renamed == baseline


def test_top_quantile_turnover_is_symmetric_when_tie_group_size_changes() -> None:
    service = FactorCrossSectionService()
    small = {"AAA", "BBB"}
    large = {"AAA", "BBB", "CCC", "DDD"}

    expansion = service._top_quantile_turnover(small, large)
    contraction = service._top_quantile_turnover(large, small)

    assert expansion == Decimal(1) / Decimal(3)
    assert contraction == expansion


def test_quantile_monotonicity_keeps_ties_in_one_bucket() -> None:
    service = FactorCrossSectionService()
    ordered = [
        ("AAA", Decimal("1"), Decimal("0.1")),
        ("BBB", Decimal("2"), Decimal("0.2")),
        ("CCC", Decimal("2"), Decimal("0.4")),
        ("DDD", Decimal("3"), Decimal("0.5")),
    ]

    monotonicity = service._quantile_monotonicity(ordered)
    reordered_tie = service._quantile_monotonicity(
        [ordered[0], ordered[2], ordered[1], ordered[3]]
    )

    assert monotonicity == reordered_tie
    assert monotonicity is not None
    assert monotonicity > Decimal("0.98")
