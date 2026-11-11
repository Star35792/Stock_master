from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import ValuationSnapshot
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.services.price_value_input_prep import PriceValueInputPrepService
from tests.test_price_value_screen import (
    SHANGHAI,
    TARGET,
    _prep_database,
    _ReportedRoe,
    _Vendor,
)


class DatedVendor(_Vendor):
    def iter_valuation_snapshot_pages(self):
        raise AssertionError("catch-up must never request a current quote")

    def iter_same_day_dated_valuation_pages(self, *, trade_date):
        assert trade_date == TARGET
        yield from super().iter_valuation_snapshot_pages()


def test_catchup_uses_actual_clock_dated_quotes_and_is_idempotent(tmp_path):
    engine = _prep_database(tmp_path)
    actual = datetime(2026, 9, 22, 10, 1, tzinfo=SHANGHAI)
    vendor = DatedVendor()
    service = PriceValueInputPrepService(
        source=vendor, reported_roe_service=_ReportedRoe(), clock=lambda: actual,
    )
    with Session(engine) as db:
        result = service.run(
            db, observation_mode="current_research_catchup", price_date=TARGET,
            now=actual - timedelta(days=1),
        )
        assert result["price_as_of"] == TARGET.isoformat()
        assert result["financial_as_of"] == actual.date().isoformat()
        assert datetime.fromisoformat(result["observed_at"]) == actual
        assert result["pit_verified"] is False
        assert result["execution_eligible"] is False
        assert result["valuation_mode"] == "dated_catchup"
        assert service.run(db, observation_mode="current_research_catchup") == result
        assert vendor.calls == 1
        # A failed batch must not be reused even if its job metadata looks valid.
        batch = db.get(ImportBatch, result["batch_id"])
        batch.status = "failed"
        db.commit()
        retry = service.run(db, observation_mode="current_research_catchup")
        assert retry["batch_id"] != result["batch_id"]
        assert vendor.calls == 2
        row = db.scalar(select(ValuationSnapshot))
        assert row.raw_payload["observation_mode"] == "current_research_catchup"
        assert row.raw_payload["financial_as_of"] == actual.date().isoformat()
        assert row.raw_payload["_roe_disclosed_at"].startswith("2026-09-22")
        raw = db.scalar(select(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == "price_value_valuation_observation",
            RawVendorSnapshot.batch_id == result["batch_id"],
        ))
        assert raw.raw_payload["observed_at"] == result["observed_at"]
    engine.dispose()


def test_catchup_rejects_arbitrary_history_and_same_evening_stays_restricted(tmp_path):
    engine = _prep_database(tmp_path)
    actual = datetime(2026, 9, 22, 10, 1, tzinfo=SHANGHAI)
    service = PriceValueInputPrepService(source=DatedVendor(), clock=lambda: actual)
    with Session(engine) as db:
        with pytest.raises(ValueError, match="latest completed"):
            service.run(db, observation_mode="current_research_catchup",
                        price_date=TARGET - timedelta(days=1))
        with pytest.raises(ValueError, match="after 21:00"):
            service.run(db)
        with pytest.raises(ValueError, match="actual observation date"):
            service.run(db, price_date=TARGET)
    engine.dispose()


def test_catchup_keeps_price_match_gate(tmp_path):
    engine = _prep_database(tmp_path)
    actual = datetime(2026, 9, 22, 10, 1, tzinfo=SHANGHAI)

    class WrongClose(DatedVendor):
        def iter_same_day_dated_valuation_pages(self, *, trade_date):
            for page, total, items, raw in super().iter_same_day_dated_valuation_pages(
                trade_date=trade_date
            ):
                for item in items:
                    item["f2"] = 99
                yield page, total, items, raw

    service = PriceValueInputPrepService(
        source=WrongClose(), reported_roe_service=_ReportedRoe(), clock=lambda: actual,
    )
    with Session(engine) as db:
        with pytest.raises(ValueError, match="no matched securities"):
            service.run(db, observation_mode="current_research_catchup")
        assert db.scalar(select(ValuationSnapshot)) is None
    engine.dispose()
