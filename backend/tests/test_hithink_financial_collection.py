from datetime import date

import pytest

from app.data_sources import HithinkFinanceDataSource
from app.services.hithink_financial_collection import (
    _raw_indicators,
    _raw_statement,
    _upsert_indicators,
)


def test_financial_data_source_maps_statement_endpoint() -> None:
    source = HithinkFinanceDataSource(api_key="test")
    calls = []
    source._get = lambda path, params: calls.append((path, params)) or {  # type: ignore[method-assign]
        "item": [{"thscode": "600519.SH"}]
    }

    rows = source.fetch_financial_statements(
        symbol="600519.sh",
        statement_type="income",
        period="annual",
        limit=4,
    )

    assert rows == [{"thscode": "600519.SH"}]
    assert calls == [
        (
            "/api/a-share/financials/income-statements",
            {"thscode": "600519.SH", "period": "annual", "limit": 4},
        )
    ]


@pytest.mark.parametrize("report", ["2025", "2025-", "2025-5", "25-1"])
def test_financial_indicator_report_validation(report: str) -> None:
    with pytest.raises(ValueError, match="report"):
        HithinkFinanceDataSource(api_key="test").fetch_financial_indicators(
            symbol="600519.SH",
            report=report,
        )


def test_financial_payload_normalization() -> None:
    item = {
        "thscode": "600519.SH",
        "period": "annual",
        "fiscal_year": 2024,
        "fiscal_period": "FY",
        "report_date_ms": 1735574400000,
        "period_end_ms": 1735574400000,
        "currency": "CNY",
        "net_profit": 100,
    }

    row = _raw_statement(1, "hithink_finance", "income", item)

    assert row.symbol == "600519.SH"
    assert row.report_key == "2024-FY"
    assert row.report_date == date(2024, 12, 31)
    assert len(row.row_hash) == 64


class FakeDb:
    def __init__(self) -> None:
        self.statements = []

    def execute(self, statement: object) -> None:
        self.statements.append(statement)


def test_financial_indicators_are_normalized_to_long_rows() -> None:
    payload = {
        "thscode": "600519.SH",
        "report": "2025-4",
        "abilities": [
            {
                "ability": "profitability",
                "indicators": [
                    {"index_id": "roe", "value": "32.5"},
                    {"index_id": "margin", "value": None},
                ],
            }
        ],
    }
    raw = _raw_indicators(1, "hithink_finance", payload)
    db = FakeDb()

    count = _upsert_indicators(db, "hithink_finance", payload)  # type: ignore[arg-type]

    assert raw.report_key == "2025-4"
    assert count == 2
    assert len(db.statements) == 1
