from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.data_sources.tdx_official import TdxPackage, _to_decimal
from app.database.session import initialize_database
from app.models.raw import ImportBatch
from app.services.tdx_daily_collection import TdxDailyBarCollectionService


@pytest.fixture
def db():
    engine = create_engine("duckdb:///:memory:")
    initialize_database(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


class FailingSource:
    name = "tdx_official"
    source_type = "vendor"

    def fetch_package(self, trade_date):
        raise RuntimeError("upstream timeout")


class EmptySource(FailingSource):
    def fetch_package(self, trade_date):
        return TdxPackage(trade_date=trade_date, rows=[])

    def to_daily_bar_records(self, package, **kwargs):
        return []


@pytest.mark.parametrize("source,error", [
    (FailingSource(), "upstream timeout"),
    (EmptySource(), "no comparable daily bars"),
])
def test_failed_or_empty_tdx_download_keeps_failed_batch(db, source, error):
    with pytest.raises((RuntimeError, ValueError), match=error):
        TdxDailyBarCollectionService(data_source=source).collect(  # type: ignore[arg-type]
            db, trade_date=date(2026, 11, 10)
        )

    batch = db.scalar(select(ImportBatch).where(ImportBatch.source_name == "tdx_official"))
    assert batch is not None
    assert batch.status == "failed"
    assert batch.request_params["trade_date"] == "2026-11-10"
    assert error in batch.error_message
    assert batch.accepted_count == 0


def test_tdx_decimal_parser_keeps_decimal_precision():
    value = Decimal("123456789.123456789012345678")
    assert _to_decimal(value) == value
    assert _to_decimal("NaN") is None
    assert _to_decimal("Infinity") is None
