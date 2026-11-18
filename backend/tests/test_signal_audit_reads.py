from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import duckdb
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.repositories.factor.factor_value import FactorValueRepository
from app.repositories.research_read import WarmResearchReadRepository
from app.repositories.strategy.backtest import BacktestRepository
from app.services.strategy_research import StrategyResearchService
from app.strategies.templates import FactorScoreSpec

DAY = date(2026, 1, 5)


def test_warm_read_limit_is_applied_in_sql_before_fetch():
    connection = duckdb.connect(":memory:")
    try:
        connection.execute("CREATE SCHEMA factor")
        connection.execute("""CREATE TABLE factor.factor_value AS
            SELECT DATE '2026-01-05' AS trade_date, 'S' || i AS symbol,
            1.25::DECIMAL(18,6) AS value, 7 AS run_id, 'f' AS factor_name,
            'v1' AS factor_version FROM range(20) t(i)""")
        repository = WarmResearchReadRepository(connection)
        rows = repository.list_factor_values(
            factor_name="f", factor_version="v1", start_date=DAY, end_date=DAY, limit=3
        )
        assert len(rows) == 3
        assert isinstance(rows[0].value, Decimal)
        assert len(repository.list_factor_values(
            factor_name="f", factor_version="v1", start_date=DAY, end_date=DAY
        )) == 20
        with pytest.raises(ValueError):
            repository.list_factor_values(
                factor_name="f", factor_version="v1", start_date=DAY, end_date=DAY, limit=0
            )
    finally:
        connection.close()


def test_control_query_carries_sql_limit_and_default_stays_unlimited():
    db = Mock()
    db.scalars.return_value = []
    repository = FactorValueRepository()
    options = dict(factor_name="f", factor_version="v1", start_date=DAY, end_date=DAY)
    repository.list_for_cross_section(db, **options, limit=4)
    statement = db.scalars.call_args.args[0]
    assert statement._limit_clause.value == 4
    repository.list_for_cross_section(db, **options)
    assert db.scalars.call_args.args[0]._limit_clause is None


@pytest.mark.parametrize("source", ["control", "warm"])
def test_frame_cap_is_shared_across_factors_and_reader_closes(source):
    service = StrategyResearchService()
    service.research_read_source = source
    limits, closed = [], []

    def read(**kwargs):
        limits.append(kwargs["limit"])
        return [SimpleNamespace(trade_date=DAY, symbol=f"S{i}", value=Decimal(1), run_id=1)
                for i in range(min(3, kwargs["limit"]))]

    @contextmanager
    def reader(db):
        try:
            yield read
        finally:
            closed.append(True)

    service._factor_read = reader
    with pytest.raises(ValueError, match="上限"):
        service.load_signal_audit_frame(None, factor_specs=[FactorScoreSpec("a", "1"),
            FactorScoreSpec("b", "1")], start_date=DAY, end_date=DAY, max_rows=4)
    assert limits == [5, 2]
    assert closed == [True]


def test_run_header_measures_json_without_loading_it():
    engine = create_engine("duckdb:///:memory:")
    try:
        with Session(engine) as db:
            db.execute(text("CREATE SCHEMA strategy"))
            db.execute(text("CREATE TABLE strategy.backtest_run (id BIGINT, parameters JSON)"))
            db.execute(text("INSERT INTO strategy.backtest_run VALUES (1, '{\"signals\": []}')"))
            header = BacktestRepository().signal_audit_header(db, run_id=1)
            assert header["parameter_chars"] == len('{"signals": []}')
            assert "parameters" not in header
            assert BacktestRepository().signal_audit_header(db, run_id=2) is None
    finally:
        engine.dispose()
