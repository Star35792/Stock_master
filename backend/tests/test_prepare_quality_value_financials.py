import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database.base import Base
from app.models.fundamental import FinancialStatement
from app.models.raw import RawFinancialPayload
from scripts.ingest.prepare_quality_value_financials import (
    coverage,
    fetch_symbol,
    replay_phase,
    required_periods,
    save_report,
)


class Source:
    def fetch_financial_statements(self, *, symbol, statement_type, period, limit):
        ends = required_periods(date(2026, 10, 28), period)
        return [
            dict(
                thscode=symbol,
                period=period,
                fiscal_year=end.year,
                fiscal_period=str(end.month // 3),
                currency="CNY",
                net_profit=100,
                report_date_ms=int(datetime(2026, 10, 20, tzinfo=UTC).timestamp() * 1000),
                period_end_ms=int(
                    datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp() * 1000
                ),
            )
            for end in sorted(ends)
        ]


@pytest.fixture
def db():
    engine = create_engine("duckdb:///:memory:")
    with engine.begin() as connection:
        for schema in sorted({t.schema for t in Base.metadata.tables.values()}):
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_cache_replay_uses_real_raw_and_fundamental_pipeline(db):
    symbol, fetched = fetch_symbol("600519.SH", ["annual", "quarterly"], Source)
    annual = replay_phase(db, symbol, "annual", fetched["annual"], date(2026, 10, 28))
    assert annual["status"] == "success"
    assert annual["batch_no"].startswith("hithink_financial_")
    assert db.scalar(select(func.count()).select_from(RawFinancialPayload)) == 9
    assert db.scalar(select(func.count()).select_from(FinancialStatement)) == 9
    quarterly = replay_phase(db, symbol, "quarterly", fetched["quarterly"], date(2026, 10, 28))
    assert quarterly["status"] == "success"
    assert quarterly["raw_rows"] == quarterly["statement_rows"] == 21
    assert coverage(db, symbol, "quarterly", date(2026, 10, 28))["complete"]


def test_fetch_error_keeps_annual_and_does_not_expose_secret(db):
    class PartialSource(Source):
        def fetch_financial_statements(self, **kwargs):
            if kwargs["period"] == "quarterly":
                raise RuntimeError("api_key=secret")
            return super().fetch_financial_statements(**kwargs)

    symbol, fetched = fetch_symbol("600519.SH", ["annual", "quarterly"], PartialSource)
    assert (
        replay_phase(db, symbol, "annual", fetched["annual"], date(2026, 10, 28))["status"]
        == "success"
    )
    failed = replay_phase(db, symbol, "quarterly", fetched["quarterly"], date(2026, 10, 28))
    assert failed["status"] == "fetch_failed"
    assert "secret" not in str(failed)
    assert db.scalar(select(func.count()).select_from(FinancialStatement)) == 9


def test_empty_or_old_rows_are_not_complete(db):
    class EmptySource(Source):
        def fetch_financial_statements(self, **kwargs):
            return []

    _, result = fetch_symbol("600519.SH", ["annual"], EmptySource)
    assert result["annual"]["status"] == "fetch_failed"
    assert not coverage(db, "600519.SH", "annual", date(2026, 10, 28))["complete"]
    assert required_periods(date(2026, 10, 28), "annual") == {
        date(2023, 12, 31),
        date(2024, 12, 31),
        date(2025, 12, 31),
    }
    assert len(required_periods(date(2026, 10, 28), "quarterly")) == 7


def test_report_retries_transient_windows_reader_lock(tmp_path, monkeypatch):
    output = tmp_path / 'report.json'
    original = Path.replace
    attempts = []

    def replace(path, target):
        attempts.append(path)
        if len(attempts) == 1:
            raise PermissionError('transient reader handle')
        return original(path, target)

    monkeypatch.setattr(Path, 'replace', replace)
    report = {'status': 'running', 'symbols': []}
    save_report(output, report)
    assert len(attempts) == 2
    assert json.loads(output.read_text(encoding='utf-8'))['status'] == 'running'
