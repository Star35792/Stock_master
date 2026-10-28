from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from app.config.settings import settings
from app.data_sources import corporate_action_evidence as source_module
from app.data_sources.corporate_action_evidence import CorporateActionResponse
from app.database.base import Base
from app.database.session import SCHEMAS
from app.models.market import CorporateAction
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.scheduler import auto_data_sync as scheduler
from app.services.corporate_action_evidence import CorporateActionEvidenceService
from app.services.delist_evidence import DelistEvidenceService


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'evidence.duckdb'}")
    with engine.begin() as connection:
        for schema in SCHEMAS:
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def _service(rows: list[dict]) -> CorporateActionEvidenceService:
    service = CorporateActionEvidenceService()
    service.sources["akshare"] = SimpleNamespace(
        name="akshare_cninfo",
        source_type="akshare",
        endpoint="stock_dividend_cninfo",
        fetch=lambda symbol: CorporateActionResponse(
            "akshare_cninfo", "stock_dividend_cninfo", "test", rows
        ),
    )
    return service


def test_raw_evidence_is_idempotent_and_does_not_publish_market(db):
    rows = [
        {
            "股权登记日": "2026-06-10",
            "除权日": "2026-06-11",
            "派息日": "2026-06-12",
            "实施方案公告日期": "2026-06-01",
        }
    ]
    service = _service(rows)
    first = service.collect_one(db, source="akshare", symbol="600519.SH")
    second = service.collect_one(db, source="akshare", symbol="600519.SH")

    assert first["new_snapshot"] is True
    assert second["new_snapshot"] is False
    assert len(db.scalars(select(RawVendorSnapshot)).all()) == 1
    assert len(db.scalars(select(ImportBatch)).all()) == 2
    assert db.scalars(select(CorporateAction)).all() == []
    snapshot = db.scalar(select(RawVendorSnapshot))
    assert snapshot.raw_payload["rows"][0]["派息日"] == "2026-06-12"
    assert snapshot.raw_payload["pit_verified"] is False


def test_failure_is_audited_without_leaking_token(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "sentinel-secret")
    service = _service([])

    def fail(symbol):
        raise RuntimeError("supplier error sentinel-secret")

    service.sources["akshare"].fetch = fail
    with pytest.raises(RuntimeError):
        service.collect_one(db, source="akshare", symbol="600519.SH")

    batch = db.scalar(select(ImportBatch))
    assert batch.status == "failed"
    assert "sentinel-secret" not in batch.error_message
    assert db.scalars(select(RawVendorSnapshot)).all() == []


def test_pending_queue_uses_missing_market_dates_and_rotates(db):
    for symbol in ("600519.SH", "000001.SZ"):
        db.add(
            CorporateAction(
                symbol=symbol,
                ex_date=date(2026, 6, 11),
                action_type="cash_dividend",
                source_name="hithink_finance_dump",
            )
        )
    db.commit()
    service = _service([])
    first = service.pending_symbols(db, source="akshare", limit=1)
    assert first == ["000001.SZ"]
    service.collect_one(db, source="akshare", symbol=first[0])
    assert service.pending_symbols(db, source="akshare", limit=1) == ["600519.SH"]


def test_tushare_token_stays_out_of_raw_evidence(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "sentinel-secret")
    response = {
        "code": 0,
        "data": {
            "fields": ["ts_code", "record_date", "ex_date", "pay_date", "imp_ann_date"],
            "items": [["600519.SH", "20260610", "20260611", "20260612", "20260601"]],
        },
    }

    class FakeHttpResponse(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(
        source_module,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(json.dumps(response).encode()),
    )
    outcome = CorporateActionEvidenceService().collect_one(
        db, source="tushare", symbol="600519.SH"
    )
    batch = db.scalar(select(ImportBatch))
    snapshot = db.scalar(select(RawVendorSnapshot))
    assert outcome["rows"] == 1
    assert snapshot.raw_payload["rows"][0]["pay_date"] == "20260612"
    assert "sentinel-secret" not in json.dumps(batch.request_params)
    assert "sentinel-secret" not in json.dumps(snapshot.raw_payload)


def test_optional_evidence_step_runs_after_daily_closure(monkeypatch):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(settings, "corporate_action_evidence_sync_enabled", True)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(
        scheduler,
        "AutoDataSyncService",
        lambda: SimpleNamespace(
            sync=lambda db: SimpleNamespace(advanced=True, end_date=date(2026, 10, 26))
        ),
    )
    monkeypatch.setattr(
        scheduler,
        "run_daily_loop",
        lambda **kwargs: calls.append("closure") or SimpleNamespace(failed=False),
    )
    monkeypatch.setattr(
        scheduler,
        "CorporateActionEvidenceService",
        lambda: SimpleNamespace(collect_pending=lambda db: calls.append("evidence") or []),
    )

    scheduler.run_auto_data_sync()

    assert calls == ["closure", "evidence"]


def test_daily_source_failure_does_not_suppress_raw_evidence(monkeypatch):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def failed_daily_sync(db):
        calls.append("daily")
        raise RuntimeError("daily source unavailable")

    monkeypatch.setattr(settings, "corporate_action_evidence_sync_enabled", True)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(
        scheduler,
        "AutoDataSyncService",
        lambda: SimpleNamespace(sync=failed_daily_sync),
    )
    monkeypatch.setattr(
        scheduler,
        "CorporateActionEvidenceService",
        lambda: SimpleNamespace(collect_pending=lambda db: calls.append("evidence") or []),
    )

    with pytest.raises(RuntimeError, match="daily source unavailable"):
        scheduler.run_auto_data_sync()

    assert calls == ["daily", "evidence"]


def test_dividend_fallback_on_supplier_failure_and_empty_rows(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "sentinel-secret")
    service = _service([{
        "股权登记日": "2026-06-10", "除权日": "2026-06-11",
        "派息日": "2026-06-12",
    }])
    service.sources["tushare"] = SimpleNamespace(
        name="tushare", source_type="api", endpoint="dividend",
        fetch=lambda symbol: (_ for _ in ()).throw(RuntimeError("quota sentinel-secret")),
    )
    outcome = service.collect_with_fallback(db, symbol="600519.SH")
    assert outcome["status"] == "success"
    assert outcome["selected_source"] == "akshare"
    assert [item["status"] for item in outcome["attempts"]] == ["failed", "complete"]
    assert "sentinel-secret" not in json.dumps(outcome)
    assert [batch.status for batch in db.scalars(select(ImportBatch)).all()] == [
        "failed", "success"
    ]
    selected_batch = db.scalar(select(ImportBatch).where(
        ImportBatch.source_name == "akshare_cninfo"
    ))
    assert selected_batch.request_params["fallback_outcome"]["selected_source"] == "akshare"
    assert db.scalars(select(CorporateAction)).all() == []

    service.sources["tushare"].fetch = lambda symbol: CorporateActionResponse(
        "tushare", "dividend", "test", []
    )
    second = service.collect_with_fallback(db, symbol="000001.SZ")
    assert second["status"] == "success"
    assert [item["source"] for item in second["attempts"]] == ["akshare"]


def test_empty_primary_response_triggers_dividend_fallback(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "test-secret")
    service = _service([{
        "股权登记日": "2026-06-10", "除权日": "2026-06-11",
        "派息日": "2026-06-12",
    }])
    service.sources["tushare"] = SimpleNamespace(
        name="tushare", source_type="api", endpoint="dividend",
        fetch=lambda symbol: CorporateActionResponse(
            "tushare", "dividend", "test", []
        ),
    )
    outcome = service.collect_with_fallback(db, symbol="600519.SH")
    assert [item["status"] for item in outcome["attempts"]] == ["empty", "complete"]


def test_eastmoney_dividend_dates_are_only_partial_evidence(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", None)
    service = _service([])
    service.sources["eastmoney"] = SimpleNamespace(
        name="eastmoney_via_akshare", source_type="akshare",
        endpoint="stock_fhps_detail_em",
        fetch=lambda symbol: CorporateActionResponse(
            "eastmoney_via_akshare", "stock_fhps_detail_em", "test", [{
                "股权登记日": "2026-06-10", "除权除息日": "2026-06-11"
            }]
        ),
    )
    outcome = service.collect_with_fallback(db, symbol="600519.SH")
    assert outcome["status"] == "partial"
    assert outcome["selected_source"] == "eastmoney"
    assert outcome["attempts"][-1]["status"] == "partial"
    assert db.scalars(select(CorporateAction)).all() == []


def test_delist_fallback_preserves_sh_suspension_semantics(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "sentinel-secret")
    service = DelistEvidenceService()
    service.sources["tushare"] = SimpleNamespace(
        name="tushare", source_type="api", endpoint="stock_basic",
        fetch=lambda: (_ for _ in ()).throw(RuntimeError("quota sentinel-secret")),
    )
    service.sources["akshare"] = SimpleNamespace(
        name="akshare_exchange", source_type="akshare",
        fetch=lambda exchange: ([{"证券代码": "000001", "终止上市日期": "2020-01-02"}]
                                if exchange == "SZ" else
                                [{"公司代码": "600001", "暂停上市日期": "2020-01-02"}]),
    )
    sz = service.collect_with_fallback(db, exchange="SZ")
    sh = service.collect_with_fallback(db, exchange="SH")
    assert sz["selected_source"] == "akshare"
    assert sz["status"] == "success"
    assert sh["selected_source"] is None
    assert sh["status"] == "partial"
    assert "sentinel-secret" not in json.dumps([sz, sh])
    assert len(db.scalars(select(RawVendorSnapshot)).all()) == 2


def test_daily_delist_collection_uses_one_primary_request_and_cools_down(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "test-secret")
    service = DelistEvidenceService()
    calls = []

    def fetch_tushare():
        calls.append("tushare")
        return [{"ts_code": "600001.SH", "delist_date": "20200102"}]

    def fetch_exchange(exchange):
        calls.append(exchange)
        return [{"证券代码": "000001", "终止上市日期": "2020-01-02"}]

    service.sources["tushare"] = SimpleNamespace(
        name="tushare", source_type="api", endpoint="stock_basic",
        fetch=fetch_tushare,
    )
    service.sources["akshare"] = SimpleNamespace(
        name="akshare_exchange", source_type="akshare", fetch=fetch_exchange,
    )
    first = service.collect_due(db)
    second = service.collect_due(db)
    assert [item["selected_source"] for item in first] == ["tushare", "akshare"]
    assert second == []
    assert calls == ["tushare", "SZ"]
    primary_batch = db.scalar(select(ImportBatch).where(
        ImportBatch.source_name == "tushare"
    ))
    assert primary_batch.request_params["fallback_outcomes"]["SH"]["status"] == "success"


def test_dividend_fallback_all_sources_fail_is_explicit(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", None)
    service = _service([])
    service.sources["akshare"].fetch = lambda symbol: (_ for _ in ()).throw(
        RuntimeError("offline")
    )
    service.sources["eastmoney"].fetch = service.sources["akshare"].fetch
    result = service.collect_with_fallback(db, symbol="600519.SH")
    assert result["status"] == "failed"
    assert result["selected_source"] is None
    assert len(result["attempts"]) == 2
    assert all(batch.status == "failed" for batch in db.scalars(select(ImportBatch)))


def test_dividend_fallback_matches_the_missing_market_event(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", None)
    db.add(CorporateAction(
        symbol="600519.SH", ex_date=date(2026, 6, 11),
        action_type="cash_dividend", source_name="hithink_finance_dump",
    ))
    db.commit()
    service = _service([{
        "股权登记日": "2025-06-10", "除权日": "2025-06-11",
        "派息日": "2025-06-12",
    }])
    service.sources["eastmoney"] = SimpleNamespace(
        name="eastmoney_via_akshare", source_type="akshare",
        endpoint="stock_fhps_detail_em",
        fetch=lambda symbol: CorporateActionResponse(
            "eastmoney_via_akshare", "stock_fhps_detail_em", "test", [{
                "股权登记日": "2026-06-10", "除权除息日": "2026-06-11"
            }]
        ),
    )
    outcome = service.collect_with_fallback(db, symbol="600519.SH")
    assert outcome["status"] == "partial"
    assert [item["status"] for item in outcome["attempts"]] == ["empty", "partial"]


def test_auto_sync_runs_lifecycle_and_turnover_evidence(monkeypatch):
    calls = []

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(settings, "corporate_action_evidence_sync_enabled", False)
    monkeypatch.setattr(settings, "delist_evidence_sync_enabled", True)
    monkeypatch.setattr(settings, "turnover_evidence_sync_enabled", True)
    monkeypatch.setattr(scheduler, "SessionLocal", Session)
    monkeypatch.setattr(scheduler, "AutoDataSyncService", lambda: SimpleNamespace(
        sync=lambda db: SimpleNamespace(advanced=True, end_date=date(2026, 10, 26))
    ))
    monkeypatch.setattr(scheduler, "run_daily_loop", lambda **kwargs:
                        calls.append("closure") or SimpleNamespace(failed=False))
    monkeypatch.setattr(scheduler, "DelistEvidenceService", lambda: SimpleNamespace(
        collect_due=lambda db: calls.append("delist") or []
    ))
    monkeypatch.setattr(scheduler, "TurnoverEvidenceService", lambda: SimpleNamespace(
        collect_pending=lambda db, trade_date, limit:
        calls.append(("turnover", trade_date, limit)) or []
    ))
    scheduler.run_auto_data_sync()
    assert calls == ["closure", "delist", (
        "turnover", date(2026, 10, 26), settings.turnover_evidence_symbols_per_run
    )]


def test_one_complete_dividend_does_not_certify_other_pending_events(db, monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", None)
    for event_date in (date(2026, 6, 11), date(2026, 12, 11)):
        db.add(CorporateAction(
            symbol="600519.SH", ex_date=event_date,
            action_type="cash_dividend", source_name="hithink_finance_dump",
        ))
    db.commit()
    service = _service([{
        "股权登记日": "2026-06-10", "除权日": "2026-06-11",
        "派息日": "2026-06-12",
    }])
    service.sources["eastmoney"] = SimpleNamespace(
        name="eastmoney_via_akshare", source_type="akshare",
        endpoint="stock_fhps_detail_em",
        fetch=lambda symbol: CorporateActionResponse(
            "eastmoney_via_akshare", "stock_fhps_detail_em", "test", []
        ),
    )
    result = service.collect_with_fallback(db, symbol="600519.SH")
    assert result["status"] == "partial"
    assert result["attempts"][0]["status"] == "partial"


def test_tushare_candidate_is_not_lost_when_queues_differ(monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "test-secret")
    monkeypatch.setattr(settings, "corporate_action_akshare_symbols_per_run", 2)
    monkeypatch.setattr(settings, "corporate_action_tushare_symbols_per_run", 1)
    service = CorporateActionEvidenceService()
    service.pending_symbols = lambda db, *, source, limit: (
        ["600519.SH"] if source == "tushare" else ["000001.SZ", "000002.SZ"]
    )
    calls = []
    service.collect_with_fallback = lambda db, *, symbol, try_tushare: (
        calls.append((symbol, try_tushare)) or {"status": "success"}
    )
    service.collect_pending(None)
    assert calls == [("600519.SH", True), ("000001.SZ", False)]
