import json
from datetime import date
from hashlib import sha256

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import TradingCalendar
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.raw import RawDailyBarImportRepository
from app.services.calendar_completion import CalendarCompletionService
from tests.test_price_value_screen import _prep_database


def fixture_source(db):
    raw = RawDailyBarImportRepository()
    items = [{"exchange": exchange, "trade_date": day, "is_open": True}
             for exchange in ("SH", "SZ", "BJ")
             for day in ("2026-09-21", "2026-09-23")]
    payload = {"offset": 0, "items": items}
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    batch = raw.create_batch(
        db, batch_no="calendar_test", source_name="hithink_finance", source_type="api",
        dataset="security_and_trading_calendar", request_params={"raw_page_hashes": [digest]},
    )
    db.flush()
    db.add(RawVendorSnapshot(batch_id=batch.id, source_name="hithink_finance",
                            dataset="hithink_calendar_reference",
                            snapshot_date=date(2026, 9, 23), raw_payload=payload,
                            row_hash=digest))
    raw.finish_batch(db, batch, status="success", row_count=6,
                     accepted_count=6, rejected_count=0)
    db.commit()


def test_calendar_complement_bounds_conflicts_hash_and_idempotence(tmp_path):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        fixture_source(db)
        service = CalendarCompletionService()
        # Existing fixture says 9/22 is open, so inference must not overwrite it.
        with pytest.raises(ValueError, match="conflicts"):
            service.run(db, start=date(2026, 9, 21), end=date(2026, 9, 23))
        for row in db.scalars(select(TradingCalendar).where(
            TradingCalendar.trade_date == date(2026, 9, 22),
        )):
            db.delete(row)
        db.commit()
        result = service.run(db, start=date(2026, 9, 21), end=date(2026, 9, 23))
        assert result["inserted"] == 6
        assert service.run(db, start=date(2026, 9, 21), end=date(2026, 9, 23))["inserted"] == 0
        assert all(not r.is_open for r in db.scalars(select(TradingCalendar).where(
            TradingCalendar.trade_date == date(2026, 9, 22),
        )))
        with pytest.raises(ValueError, match="outside"):
            service.run(db, start=date(2026, 9, 20), end=date(2026, 9, 23))
        page = db.scalar(select(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == "hithink_calendar_reference",
        ))
        page.raw_payload = {**page.raw_payload, "offset": 1}
        db.commit()
        with pytest.raises(ValueError, match="unverified"):
            service.run(db, start=date(2026, 9, 21), end=date(2026, 9, 23))
    engine.dispose()


@pytest.mark.parametrize("case", ["failed", "missing_exchange", "truncated"])
def test_calendar_rejects_unverified_or_inconsistent_sources(tmp_path, case):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        fixture_source(db)
        batch = db.scalar(select(ImportBatch).where(ImportBatch.batch_no == "calendar_test"))
        page = db.scalar(select(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == "hithink_calendar_reference",
        ))
        if case == "failed":
            batch.status = "failed"
        else:
            payload = dict(page.raw_payload)
            if case == "missing_exchange":
                payload["items"] = [r for r in payload["items"] if r["exchange"] != "BJ"]
            else:
                payload["offset"] = 500
            page.raw_payload = payload
            digest = sha256(json.dumps(
                payload, sort_keys=True, ensure_ascii=False,
            ).encode()).hexdigest()
            page.row_hash = digest
            batch.request_params = {"raw_page_hashes": [digest]}
        db.commit()
        with pytest.raises(ValueError):
            CalendarCompletionService().run(
                db, start=date(2026, 9, 21), end=date(2026, 9, 23),
            )
    engine.dispose()
