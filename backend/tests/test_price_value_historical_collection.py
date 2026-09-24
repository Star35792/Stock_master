import json
from datetime import date

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.data_sources.eastmoney import EastmoneyDataSource
from app.database.session import initialize_database
from app.models.factor import FactorValue
from app.models.market import ValuationSnapshot
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.services.price_value_historical_collection import (
    DATASET,
    PriceValueHistoricalCollectionService,
)

TARGET = date(2026, 9, 22)


class HistoricalSource:
    name = "eastmoney"
    source_type = "api"

    def __init__(self, *, wrong_date=False):
        self.wrong_date = wrong_date

    def fetch_historical_valuation_page(self, *, trade_date, page, page_size):
        assert (trade_date, page, page_size) == (TARGET, 1, 500)
        row = {
            "SECURITY_CODE": "000001",
            "SECUCODE": "000001.SZ",
            "TRADE_DATE": "2026-09-21 00:00:00" if self.wrong_date else "2026-09-22 00:00:00",
            "CLOSE_PRICE": 11.71,
            "PE_TTM": 5.22891236,
            "PB_MRQ": 0.48534068,
        }
        payload = {"success": True, "result": {"count": 1, "pages": 1, "data": [row]}}
        return payload, json.dumps(payload, separators=(",", ":"))


class DuplicateSource(HistoricalSource):
    def fetch_historical_valuation_page(self, *, trade_date, page, page_size):
        row = {
            "SECURITY_CODE": "000001",
            "SECUCODE": "000001.SZ",
            "TRADE_DATE": "2026-09-22 00:00:00",
            "CLOSE_PRICE": 11.71,
            "PE_TTM": 5.22,
            "PB_MRQ": 0.48,
        }
        payload = {"success": True, "result": {"count": 2, "pages": 1, "data": [row, row]}}
        return payload, json.dumps(payload)


class InterruptedSource(HistoricalSource):
    def fetch_historical_valuation_page(self, *, trade_date, page, page_size):
        if page == 2:
            raise OSError("supplier page unavailable")
        rows = [
            {
                "SECURITY_CODE": f"{index:06d}",
                "SECUCODE": f"{index:06d}.SZ",
                "TRADE_DATE": "2026-09-22 00:00:00",
                "CLOSE_PRICE": 1,
                "PE_TTM": 5,
                "PB_MRQ": 1,
            }
            for index in range(1, 501)
        ]
        payload = {"success": True, "result": {"count": 501, "pages": 2, "data": rows}}
        return payload, json.dumps(payload)


def test_historical_adapter_uses_explicit_date_and_preserves_response(monkeypatch):
    source = EastmoneyDataSource()
    calls = []

    def request(host, path, params):
        calls.append((host, path, params))
        return {"success": True}, '{"success":true}'

    monkeypatch.setattr(source, "_get_json_with_raw", request)
    parsed, raw = source.fetch_historical_valuation_page(trade_date=TARGET, page=1)
    assert parsed == {"success": True}
    assert raw == '{"success":true}'
    assert calls[0][1] == "/api/data/v1/get"
    assert calls[0][2]["filter"] == "(TRADE_DATE='2026-09-22')"
    assert calls[0][2]["reportName"] == "RPT_VALUEANALYSIS_DET"


def test_historical_collection_keeps_versions_in_raw_and_never_publishes(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'historical.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        service = PriceValueHistoricalCollectionService(source=HistoricalSource())
        first = service.collect(db, trade_date=TARGET)
        second = service.collect(db, trade_date=TARGET)
        assert first["supplier_total"] == second["supplier_total"] == 1
        assert first["batch_id"] != second["batch_id"]
        assert first["pit_verified"] is False
        assert first["publication_scope"] == "raw_only"
        assert db.scalar(select(func.count()).select_from(ImportBatch).where(
            ImportBatch.dataset == DATASET, ImportBatch.status == "success"
        )) == 2
        raw_rows = list(db.scalars(select(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == DATASET
        )))
        assert len(raw_rows) == 2
        assert raw_rows[0].raw_payload["response_json"].startswith('{"success":true')
        assert raw_rows[0].raw_payload["fetched_at_utc"]
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(FactorValue)) == 0
    engine.dispose()


def test_wrong_supplier_date_retains_failed_raw_page(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'wrong-date.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        service = PriceValueHistoricalCollectionService(source=HistoricalSource(wrong_date=True))
        with pytest.raises(ValueError, match="wrong trade date"):
            service.collect(db, trade_date=TARGET)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.dataset == DATASET))
        assert batch.status == "failed"
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot).where(
            RawVendorSnapshot.batch_id == batch.id
        )) == 1
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
    engine.dispose()


def test_duplicate_supplier_code_fails_without_market_publication(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'duplicate.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        service = PriceValueHistoricalCollectionService(source=DuplicateSource())
        with pytest.raises(ValueError, match="duplicate"):
            service.collect(db, trade_date=TARGET)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.dataset == DATASET))
        assert batch.status == "failed"
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 1
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
    engine.dispose()


def test_second_page_failure_keeps_first_page_and_failed_batch(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'interrupted.duckdb'}")
    initialize_database(engine)
    with Session(engine) as db:
        service = PriceValueHistoricalCollectionService(source=InterruptedSource())
        with pytest.raises(OSError, match="unavailable"):
            service.collect(db, trade_date=TARGET)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.dataset == DATASET))
        assert batch.status == "failed"
        assert batch.row_count == 500
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot).where(
            RawVendorSnapshot.batch_id == batch.id
        )) == 1
        assert db.scalar(select(func.count()).select_from(ValuationSnapshot)) == 0
    engine.dispose()
