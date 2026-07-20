from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import ValuationFactorService


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


class FakeValuationSnapshotRepository:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.kwargs: dict | None = None

    def list_available(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.rows


class FakeFinancialStatementRepository:
    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self.rows = rows
        self.kwargs: dict | None = None

    def list_available(self, db: object, **kwargs: object) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.rows


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


def _bar(trade_date: date, symbol: str = "600519.SH") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        security_type="stock",
        trade_date=trade_date,
    )


def _valuation(
    trade_date: date,
    market_cap: str,
    symbol: str = "600519.SH",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        trade_date=trade_date,
        total_market_cap=Decimal(market_cap),
    )


def _statement(
    statement_type: str,
    metrics: dict[str, object],
    *,
    report_date: date,
    symbol: str = "600519.SH",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        statement_type=statement_type,
        fiscal_year=2025,
        fiscal_period="FY",
        report_date=report_date,
        period_end=date(2025, 12, 31),
        metrics=metrics,
    )


def test_valuation_factor_service_calculates_valuation_factors() -> None:
    valuation_repository = FakeValuationSnapshotRepository(
        [
            _valuation(date(2026, 4, 30), "5000"),
            _valuation(date(2026, 5, 6), "4000"),
        ]
    )
    statement_repository = FakeFinancialStatementRepository(
        [
            _statement(
                "balance",
                {"total_assets": "1000", "total_liabilities": "400"},
                report_date=date(2026, 4, 30),
            ),
            _statement(
                "income",
                {"operating_revenue": "1200", "net_profit": "300"},
                report_date=date(2026, 4, 30),
            ),
        ]
    )
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = ValuationFactorService(
        daily_bar_repository=FakeDailyBarRepository(
            [_bar(date(2026, 4, 30)), _bar(date(2026, 5, 6))]
        ),
        valuation_repository=valuation_repository,  # type: ignore[arg-type]
        financial_statement_repository=statement_repository,  # type: ignore[arg-type]
        definition_repository=definition_repository,
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 4, 30),
        end_date=date(2026, 5, 6),
        symbols=["600519.sh"],
        adjust_type="qfq",
        code_version="abc123",
    )

    assert result.rows_by_factor == {
        "market_cap": 1,
        "earnings_yield": 1,
        "book_to_price": 1,
        "sales_to_price": 1,
    }
    assert valuation_repository.kwargs is not None
    assert valuation_repository.kwargs["start_date"] == date(2026, 4, 16)
    assert statement_repository.kwargs == {
        "end_date": date(2026, 5, 6),
        "symbols": ["600519.SH"],
    }
    assert len(definition_repository.definitions) == 4
    assert all(run["status"] == "success" for run in run_repository.finished)

    values = {
        (item["factor_name"], item["trade_date"]): item["value"]
        for item in value_repository.values
    }
    assert ("market_cap", date(2026, 4, 30)) not in values
    assert values[("market_cap", date(2026, 5, 6))] == Decimal("5000.0000000000")
    assert values[("earnings_yield", date(2026, 5, 6))] == Decimal("0.0600000000")
    assert values[("book_to_price", date(2026, 5, 6))] == Decimal("0.1200000000")
    assert values[("sales_to_price", date(2026, 5, 6))] == Decimal("0.2400000000")


def test_valuation_factor_service_validates_inputs() -> None:
    service = ValuationFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        valuation_repository=FakeValuationSnapshotRepository([]),  # type: ignore[arg-type]
        financial_statement_repository=FakeFinancialStatementRepository([]),
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
