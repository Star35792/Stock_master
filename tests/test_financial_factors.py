from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import FinancialFactorService


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


class FakeFinancialStatementRepository:
    def __init__(self, statements: list[SimpleNamespace]) -> None:
        self.statements = statements
        self.kwargs: dict | None = None

    def list_available(
        self,
        db: object,
        **kwargs: object,
    ) -> list[SimpleNamespace]:
        self.kwargs = kwargs
        return self.statements


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


def _statement(
    statement_type: str,
    fiscal_year: int,
    metrics: dict[str, object],
    *,
    report_date: date,
    fiscal_period: str = "FY",
    symbol: str = "600519.SH",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        statement_type=statement_type,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        report_date=report_date,
        period_end=date(fiscal_year, 12, 31),
        metrics=metrics,
    )


def test_financial_factor_service_calculates_statement_factors() -> None:
    bars = [
        _bar(date(2026, 4, 30)),
        _bar(date(2026, 5, 6)),
    ]
    statements = [
        _statement(
            "balance",
            2024,
            {
                "total_assets": "800",
                "total_liabilities": "300",
                "current_assets": "400",
                "current_liabilities": "200",
            },
            report_date=date(2025, 4, 30),
        ),
        _statement(
            "income",
            2024,
            {
                "operating_revenue": "1000",
                "operating_cost": "650",
                "operating_profit": "250",
                "interest_expense": "50",
                "net_profit": "200",
            },
            report_date=date(2025, 4, 30),
        ),
        _statement(
            "cash_flow",
            2024,
            {"net_operating_cash_flow": "240"},
            report_date=date(2025, 4, 30),
        ),
        _statement(
            "balance",
            2025,
            {
                "total_assets": "1000",
                "total_liabilities": "400",
                "current_assets": "500",
                "current_liabilities": "250",
            },
            report_date=date(2026, 4, 30),
        ),
        _statement(
            "income",
            2025,
            {
                "operating_revenue": "1250",
                "operating_cost": "750",
                "operating_profit": "375",
                "interest_expense": "75",
                "net_profit": "300",
            },
            report_date=date(2026, 4, 30),
        ),
        _statement(
            "cash_flow",
            2025,
            {"net_operating_cash_flow": "360"},
            report_date=date(2026, 4, 30),
        ),
    ]
    financial_statement_repository = FakeFinancialStatementRepository(statements)
    definition_repository = FakeFactorDefinitionRepository()
    run_repository = FakeFactorRunRepository()
    value_repository = FakeFactorValueRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        financial_statement_repository=financial_statement_repository,
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
        "debt_to_assets": 2,
        "current_ratio": 2,
        "interest_coverage": 2,
        "roe_ttm": 2,
        "roa_ttm": 2,
        "gross_margin_ttm": 2,
        "operating_margin_ttm": 2,
        "ocf_to_net_income_ttm": 2,
        "accrual_ratio": 2,
        "revenue_growth_yoy": 1,
        "ocf_growth_yoy": 1,
        "net_profit_growth_yoy": 1,
    }
    assert financial_statement_repository.kwargs == {
        "end_date": date(2026, 5, 6),
        "symbols": ["600519.SH"],
    }
    assert len(definition_repository.definitions) == 12
    assert all(run["status"] == "success" for run in run_repository.finished)

    values = {
        (item["factor_name"], item["trade_date"]): item["value"]
        for item in value_repository.values
    }
    assert values[("debt_to_assets", date(2026, 4, 30))] == Decimal("0.3750000000")
    assert values[("debt_to_assets", date(2026, 5, 6))] == Decimal("0.4000000000")
    assert values[("current_ratio", date(2026, 5, 6))] == Decimal("2.0000000000")
    assert values[("interest_coverage", date(2026, 5, 6))] == Decimal("5.0000000000")
    assert values[("roe_ttm", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("roa_ttm", date(2026, 5, 6))] == Decimal("0.3000000000")
    assert values[("gross_margin_ttm", date(2026, 5, 6))] == Decimal("0.4000000000")
    assert values[("operating_margin_ttm", date(2026, 5, 6))] == Decimal("0.3000000000")
    assert values[("ocf_to_net_income_ttm", date(2026, 5, 6))] == Decimal("1.2000000000")
    assert values[("accrual_ratio", date(2026, 5, 6))] == Decimal("-0.0666666667")
    assert values[("revenue_growth_yoy", date(2026, 5, 6))] == Decimal("0.2500000000")
    assert values[("ocf_growth_yoy", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("net_profit_growth_yoy", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert ("revenue_growth_yoy", date(2026, 4, 30)) not in values
    assert all(item["factor_version"] == "v1" for item in value_repository.values)


def test_financial_factor_service_skips_missing_denominators() -> None:
    value_repository = FakeFactorValueRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([_bar(date(2026, 5, 6))]),
        financial_statement_repository=FakeFinancialStatementRepository(
            [
                _statement(
                    "balance",
                    2025,
                    {"total_assets": "0", "total_liabilities": "400"},
                    report_date=date(2026, 4, 30),
                ),
                _statement(
                    "income",
                    2025,
                    {"net_profit": "0"},
                    report_date=date(2026, 4, 30),
                ),
            ]
        ),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=["debt_to_assets", "ocf_to_net_income_ttm"],
    )

    assert result.rows_by_factor == {
        "debt_to_assets": 0,
        "ocf_to_net_income_ttm": 0,
    }
    assert value_repository.values == []


def test_financial_factor_service_validates_inputs() -> None:
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
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
