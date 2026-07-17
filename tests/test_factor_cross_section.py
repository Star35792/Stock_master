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
    def count_trusted_by_date(self, db: object, **kwargs: object) -> dict[date, int]:
        return {date(2026, 7, 17): 5}


class FakeSecurityRepository:
    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {"A": "I1", "B": "I1", "C": "I2", "D": "I2"}


def _row(symbol: str, factor_name: str, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=date(2026, 7, 17),
        factor_name=factor_name,
        factor_version="v1-none",
        value=Decimal(value),
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
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


def test_cross_section_service_validates_size_factor_pair() -> None:
    service = FactorCrossSectionService(
        value_repository=FakeFactorValueRepository([], []),
        profile_repository=FakeProfileRepository(),
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        daily_bar_repository=FakeDailyBarRepository(),
        security_repository=FakeSecurityRepository(),
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
