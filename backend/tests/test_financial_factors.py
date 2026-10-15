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


class FakeFinancialStatementRevisionRepository:
    def __init__(
        self, latest: dict[tuple[str, str, str, int, str], SimpleNamespace] | None = None
    ) -> None:
        self.latest = latest or {}
        self.kwargs: dict | None = None

    def latest_by_key(
        self,
        db: object,
        *,
        symbols: list[str] | None = None,
    ) -> dict[tuple[str, str, str, int, str], SimpleNamespace]:
        self.kwargs = {"symbols": symbols}
        return self.latest


class FakeSecurityRepository:
    def __init__(self, industries: dict[str, str] | None = None) -> None:
        self.industries = industries or {}

    def get_industries(self, db: object, symbols: set[str]) -> dict[str, str]:
        return {
            symbol: industry for symbol, industry in self.industries.items() if symbol in symbols
        }


class FakeSecurityIndustryHistoryRepository:
    def __init__(self, by_date: dict[date, dict[str, str]] | None = None) -> None:
        self.by_date = by_date or {}
        self.calls: list[dict] = []

    def industries_as_of(
        self, db: object, *, symbols: set[str] | list[str], as_of: date
    ) -> dict[str, str]:
        self.calls.append({"symbols": set(symbols), "as_of": as_of})
        resolved = self.by_date.get(as_of, {})
        return {s: v for s, v in resolved.items() if s in set(symbols)}


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
    period: str = "annual",
    symbol: str = "600519.SH",
    currency: str = "CNY",
) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        statement_type=statement_type,
        period=period,
        fiscal_year=fiscal_year,
        fiscal_period=fiscal_period,
        report_date=report_date,
        period_end=date(fiscal_year, 12, 31),
        currency=currency,
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
            2022,
            {
                "accounts_receivable": "100",
                "inventory": "120",
            },
            report_date=date(2023, 4, 30),
        ),
        _statement(
            "income",
            2022,
            {
                "operating_revenue": "729",
                "net_profit": "150",
            },
            report_date=date(2023, 4, 30),
        ),
        _statement(
            "balance",
            2024,
            {
                "total_assets": "800",
                "total_liabilities": "300",
                "interest_bearing_debt": "100",
                "current_assets": "400",
                "current_liabilities": "200",
                "accounts_receivable": "160",
                "inventory": "180",
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
                "interest_bearing_debt": "150",
                "current_assets": "500",
                "current_liabilities": "250",
                "accounts_receivable": "220",
                "inventory": "270",
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
    revision_repository = FakeFinancialStatementRevisionRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository(bars),
        financial_statement_repository=financial_statement_repository,
        financial_statement_revision_repository=revision_repository,
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
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
        "operating_cash_debt": 2,
        "roe_ttm": 2,
        "roic_ttm": 2,
        "roa_ttm": 2,
        "gross_margin_ttm": 2,
        "operating_margin_ttm": 2,
        "ocf_to_net_income_ttm": 2,
        "accrual_ratio": 2,
        "revenue_growth_yoy": 1,
        "ocf_growth_yoy": 1,
        "net_profit_growth_yoy": 1,
        "roe_stability_3y": 0,
        "revenue_cagr_3y": 1,
        "net_profit_cagr_3y": 1,
        "receivable_growth_revenue_gap": 1,
        "inventory_growth_revenue_gap": 1,
        "financial_anomaly_score": 2,
    }
    assert financial_statement_repository.kwargs == {
        "end_date": date(2026, 5, 6),
        "symbols": ["600519.SH"],
    }
    assert len(definition_repository.definitions) == 20
    assert all(run["status"] == "success" for run in run_repository.finished)
    # Every run must bind to the exact statement content it consumed and to the
    # latest revision hash per statement key.
    for run in run_repository.runs:
        assert run.metadata["report_date_semantics"] == ("source_provided_announcement_date")
        assert len(run.metadata["statement_content_digest"]) == 64
        assert run.metadata["statement_revision_binding"] == {
            "policy": "latest_revision_row_hash",
            "revisions": {},
        }
        assert run.metadata["industry_classification"]["policy"] == "pit_history"
        assert run.metadata["financial_sector_policy"]["excluded_symbols"] == []
    assert run_repository.runs[0].parameters["availability_policy"] == (
        "announcement_date_before_trade_date"
    )

    values = {
        (item["factor_name"], item["trade_date"]): item["value"] for item in value_repository.values
    }
    assert values[("debt_to_assets", date(2026, 4, 30))] == Decimal("0.3750000000")
    assert values[("debt_to_assets", date(2026, 5, 6))] == Decimal("0.4000000000")
    assert values[("current_ratio", date(2026, 5, 6))] == Decimal("2.0000000000")
    assert values[("interest_coverage", date(2026, 5, 6))] == Decimal("5.0000000000")
    assert values[("operating_cash_debt", date(2026, 5, 6))] == Decimal("2.4000000000")
    assert values[("roe_ttm", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("roic_ttm", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("roa_ttm", date(2026, 5, 6))] == Decimal("0.3000000000")
    assert values[("gross_margin_ttm", date(2026, 5, 6))] == Decimal("0.4000000000")
    assert values[("operating_margin_ttm", date(2026, 5, 6))] == Decimal("0.3000000000")
    assert values[("ocf_to_net_income_ttm", date(2026, 5, 6))] == Decimal("1.2000000000")
    assert values[("accrual_ratio", date(2026, 5, 6))] == Decimal("-0.0666666667")
    assert values[("revenue_growth_yoy", date(2026, 5, 6))] == Decimal("0.2500000000")
    assert values[("ocf_growth_yoy", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("net_profit_growth_yoy", date(2026, 5, 6))] == Decimal("0.5000000000")
    assert values[("revenue_cagr_3y", date(2026, 5, 6))] == Decimal("0.1969081611")
    assert values[("net_profit_cagr_3y", date(2026, 5, 6))] == Decimal("0.2599210499")
    assert values[("receivable_growth_revenue_gap", date(2026, 5, 6))] == Decimal("0.1250000000")
    assert values[("inventory_growth_revenue_gap", date(2026, 5, 6))] == Decimal("0.2500000000")
    assert values[("financial_anomaly_score", date(2026, 5, 6))] == Decimal("0.1666666667")
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
        financial_statement_revision_repository=(FakeFinancialStatementRevisionRepository()),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=[
            "debt_to_assets",
            "operating_cash_debt",
            "roic_ttm",
            "ocf_to_net_income_ttm",
        ],
    )

    assert result.rows_by_factor == {
        "debt_to_assets": 0,
        "operating_cash_debt": 0,
        "roic_ttm": 0,
        "ocf_to_net_income_ttm": 0,
    }
    assert value_repository.values == []


def test_financial_factor_service_calculates_roe_stability_from_three_years() -> None:
    value_repository = FakeFactorValueRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([_bar(date(2026, 5, 6))]),
        financial_statement_repository=FakeFinancialStatementRepository(
            [
                _statement(
                    "balance",
                    2023,
                    {"total_assets": "800", "total_liabilities": "400"},
                    report_date=date(2024, 4, 30),
                ),
                _statement(
                    "income",
                    2023,
                    {"net_profit": "100"},
                    report_date=date(2024, 4, 30),
                ),
                _statement(
                    "balance",
                    2024,
                    {"total_assets": "1000", "total_liabilities": "500"},
                    report_date=date(2025, 4, 30),
                ),
                _statement(
                    "income",
                    2024,
                    {"net_profit": "125"},
                    report_date=date(2025, 4, 30),
                ),
                _statement(
                    "balance",
                    2025,
                    {"total_assets": "1200", "total_liabilities": "600"},
                    report_date=date(2026, 4, 30),
                ),
                _statement(
                    "income",
                    2025,
                    {"net_profit": "150"},
                    report_date=date(2026, 4, 30),
                ),
            ]
        ),
        financial_statement_revision_repository=(FakeFinancialStatementRevisionRepository()),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=FakeFactorRunRepository(),
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=["roe_stability_3y"],
    )

    assert result.rows_by_factor == {"roe_stability_3y": 1}
    assert value_repository.values == [
        {
            "symbol": "600519.SH",
            "trade_date": date(2026, 5, 6),
            "factor_name": "roe_stability_3y",
            "factor_version": "v1",
            "value": Decimal("0E-10"),
            "run_id": 1,
        }
    ]


def test_financial_factor_service_validates_inputs() -> None:
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([]),
        financial_statement_repository=FakeFinancialStatementRepository([]),
        financial_statement_revision_repository=(FakeFinancialStatementRevisionRepository()),
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
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


def test_financial_factor_service_excludes_financial_sector_from_structural_factors() -> None:
    """Bank-like issuers must not receive generic structural ratios."""

    value_repository = FakeFactorValueRepository()
    run_repository = FakeFactorRunRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([_bar(date(2026, 5, 6))]),
        financial_statement_repository=FakeFinancialStatementRepository(
            [
                _statement(
                    "balance",
                    2025,
                    {
                        "total_assets": "1000",
                        "total_liabilities": "900",
                        "current_assets": "500",
                        "current_liabilities": "250",
                    },
                    report_date=date(2026, 4, 30),
                ),
                _statement(
                    "income",
                    2025,
                    {"operating_revenue": "300", "net_profit": "80"},
                    report_date=date(2026, 4, 30),
                ),
                _statement(
                    "cash_flow",
                    2025,
                    {"net_operating_cash_flow": "50"},
                    report_date=date(2026, 4, 30),
                ),
            ]
        ),
        financial_statement_revision_repository=(FakeFinancialStatementRevisionRepository()),
        # Point-in-time history classifies the symbol as a bank.
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(
            {date(2026, 5, 6): {"600519.SH": "银行"}}
        ),
        # Current label disagrees; the PIT label must win.
        security_repository=FakeSecurityRepository({"600519.SH": "食品饮料"}),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=run_repository,
        value_repository=value_repository,
    )

    result = service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=[
            "debt_to_assets",
            "current_ratio",
            "gross_margin_ttm",
            "roic_ttm",
            "roe_ttm",
            "revenue_growth_yoy",
        ],
    )

    assert result.rows_by_factor == {
        "debt_to_assets": 0,
        "current_ratio": 0,
        "gross_margin_ttm": 0,
        "roic_ttm": 0,
        "roe_ttm": 1,
        "revenue_growth_yoy": 0,
    }
    policy = run_repository.runs[0].metadata["financial_sector_policy"]
    assert policy["excluded_symbols"] == ["600519.SH"]
    assert policy["version"] == "financial_sector_generic_factor_exclusion_v1"
    assert run_repository.runs[0].metadata["industry_classification"]["policy"] == ("pit_history")


def test_financial_factor_service_records_current_label_fallback() -> None:
    """No history for the trade date means a declared current-label fallback."""

    run_repository = FakeFactorRunRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([_bar(date(2026, 5, 6))]),
        financial_statement_repository=FakeFinancialStatementRepository([]),
        financial_statement_revision_repository=(FakeFinancialStatementRevisionRepository()),
        security_repository=FakeSecurityRepository({"600519.SH": "食品饮料"}),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=run_repository,
        value_repository=FakeFactorValueRepository(),
    )

    service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=["roe_ttm"],
    )

    classification = run_repository.runs[0].metadata["industry_classification"]
    assert classification["policy"] == "pit_history_with_current_label_fallback"
    assert classification["fallback_symbols"] == ["600519.SH"]
    assert classification["unresolved_symbols"] == []


def test_financial_factor_service_binds_latest_revision_hashes() -> None:
    """Run metadata binds each statement key to its latest revision hash."""

    revision = SimpleNamespace(row_hash="a" * 64)
    revision_repository = FakeFinancialStatementRevisionRepository(
        {("600519.SH", "income", "annual", 2025, "FY"): revision}
    )
    run_repository = FakeFactorRunRepository()
    service = FinancialFactorService(
        daily_bar_repository=FakeDailyBarRepository([_bar(date(2026, 5, 6))]),
        financial_statement_repository=FakeFinancialStatementRepository(
            [
                _statement(
                    "income",
                    2025,
                    {"net_profit": "100"},
                    report_date=date(2026, 4, 30),
                    period="annual",
                    fiscal_period="FY",
                )
            ]
        ),
        financial_statement_revision_repository=revision_repository,
        security_repository=FakeSecurityRepository(),
        security_industry_history_repository=FakeSecurityIndustryHistoryRepository(),
        definition_repository=FakeFactorDefinitionRepository(),
        run_repository=run_repository,
        value_repository=FakeFactorValueRepository(),
    )

    service.run(
        object(),
        start_date=date(2026, 5, 6),
        end_date=date(2026, 5, 6),
        factor_names=["roe_ttm"],
    )

    assert revision_repository.kwargs == {"symbols": ["600519.SH"]}
    assert run_repository.runs[0].metadata["statement_revision_binding"] == {
        "policy": "latest_revision_row_hash",
        "revisions": {"600519.SH|income|annual|2025|FY": "a" * 64},
    }
