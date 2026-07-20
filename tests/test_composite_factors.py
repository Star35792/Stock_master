from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import CompositeFactorService
from app.strategies import FactorScoreSpec


class FakeFactorValueRepository:
    def __init__(self, rows_by_factor: dict[tuple[str, str], list[SimpleNamespace]]) -> None:
        self.rows_by_factor = rows_by_factor
        self.queries: list[dict] = []
        self.values: list[dict] = []

    def list_for_cross_section(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.queries.append(kwargs)
        key = (str(kwargs["factor_name"]), str(kwargs["factor_version"]))
        return self.rows_by_factor.get(key, [])

    def upsert_value(self, db: object, **kwargs: object) -> None:
        self.values.append(kwargs)


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


class FakeSecurityRepository:
    def __init__(self, security_types: dict[str, str]) -> None:
        self.security_types = security_types
        self.symbols: set[str] | None = None

    def get_security_types(self, db: object, symbols: set[str]) -> dict[str, str]:
        self.symbols = symbols
        return {
            symbol: security_type
            for symbol, security_type in self.security_types.items()
            if symbol in symbols
        }


def _row(symbol: str, trade_date: date, value: str) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=trade_date,
        value=Decimal(value),
    )


def test_composite_factor_service_persists_weighted_complete_scores() -> None:
    value_repository = FakeFactorValueRepository(
        {
            ("momentum", "v1"): [
                _row("A", date(2026, 7, 1), "1"),
                _row("B", date(2026, 7, 1), "2"),
                _row("C", date(2026, 7, 1), "3"),
            ],
            ("valuation", "v1"): [
                _row("A", date(2026, 7, 1), "30"),
                _row("B", date(2026, 7, 1), "20"),
                _row("C", date(2026, 7, 1), "10"),
            ],
        }
    )
    definition_repository = FakeDefinitionRepository()
    run_repository = FakeRunRepository()
    service = CompositeFactorService(
        value_repository=value_repository,
        definition_repository=definition_repository,
        run_repository=run_repository,
        security_repository=FakeSecurityRepository(
            {"A": "stock", "B": "stock", "C": "stock"}
        ),
    )

    result = service.run(
        object(),
        factor_specs=[
            FactorScoreSpec("momentum", "v1", weight=Decimal("0.6")),
            FactorScoreSpec(
                "valuation",
                "v1",
                weight=Decimal("0.4"),
                higher_is_better=False,
            ),
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        symbols=["a", "b", "c"],
        allowed_security_types={"stock"},
        code_version="abc123",
    )

    assert result.input_rows == 6
    assert result.rows_written == 3
    assert result.run_id == 1
    assert definition_repository.definitions[0].name == "decision_score"
    assert definition_repository.definitions[0].inputs == [
        "factor.momentum:v1",
        "factor.valuation:v1",
    ]
    assert run_repository.finished == [
        {"run_id": 1, "status": "success", "rows_written": 3}
    ]
    values = {
        item["symbol"]: item["value"]
        for item in value_repository.values
    }
    assert values == {
        "A": Decimal("0E-10"),
        "B": Decimal("0.5000000000"),
        "C": Decimal("1.0000000000"),
    }


def test_composite_factor_service_requires_complete_coverage_and_filters_types() -> None:
    value_repository = FakeFactorValueRepository(
        {
            ("momentum", "v1"): [
                _row("A", date(2026, 7, 1), "1"),
                _row("B", date(2026, 7, 1), "2"),
                _row("ETF", date(2026, 7, 1), "3"),
            ],
            ("quality", "v1"): [
                _row("A", date(2026, 7, 1), "2"),
                _row("ETF", date(2026, 7, 1), "3"),
            ],
        }
    )
    service = CompositeFactorService(
        value_repository=value_repository,
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        security_repository=FakeSecurityRepository(
            {"A": "stock", "B": "stock", "ETF": "etf"}
        ),
    )

    result = service.run(
        object(),
        factor_specs=[
            FactorScoreSpec("momentum", "v1"),
            FactorScoreSpec("quality", "v1"),
        ],
        start_date=date(2026, 7, 1),
        end_date=date(2026, 7, 1),
        allowed_security_types={"stock"},
    )

    assert result.rows_written == 1
    assert value_repository.values[0]["symbol"] == "A"


def test_composite_factor_service_validates_inputs() -> None:
    service = CompositeFactorService(
        value_repository=FakeFactorValueRepository({}),
        definition_repository=FakeDefinitionRepository(),
        run_repository=FakeRunRepository(),
        security_repository=FakeSecurityRepository({}),
    )

    with pytest.raises(ValueError, match="start_date"):
        service.run(
            object(),
            factor_specs=[FactorScoreSpec("momentum", "v1")],
            start_date=date(2026, 7, 2),
            end_date=date(2026, 7, 1),
        )
    with pytest.raises(ValueError, match="factor_specs"):
        service.run(
            object(),
            factor_specs=[],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        )
    with pytest.raises(ValueError, match="factor weights"):
        service.run(
            object(),
            factor_specs=[FactorScoreSpec("momentum", "v1", weight=Decimal("0"))],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        )
    with pytest.raises(ValueError, match="duplicate factor specs"):
        service.run(
            object(),
            factor_specs=[
                FactorScoreSpec("momentum", "v1"),
                FactorScoreSpec("momentum", "v1"),
            ],
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
        )
