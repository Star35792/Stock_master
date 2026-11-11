from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import ValuationSnapshot
from app.services.price_value_input_prep import PriceValueInputPrepService
from app.services.price_value_screen import PriceValueScreenService
from tests.test_price_value_input_catchup import DatedVendor
from tests.test_price_value_screen import SHANGHAI, TARGET, _prep_database, _ReportedRoe


def test_current_scan_preserves_dual_dates_and_rejects_future_or_wrong_mode(tmp_path):
    engine = _prep_database(tmp_path)
    actual = datetime(2026, 9, 22, 10, 1, tzinfo=SHANGHAI)
    with Session(engine) as db:
        PriceValueInputPrepService(
            source=DatedVendor(), reported_roe_service=_ReportedRoe(), clock=lambda: actual,
        ).run(db, observation_mode="current_research_catchup")
        service = PriceValueScreenService(output_dir=tmp_path / "screens")
        report = service.scan(db, now=actual)
        assert report["observation_mode"] == "current_research_catchup"
        assert report["price_as_of"] == TARGET.isoformat()
        assert report["financial_as_of"] == actual.date().isoformat()
        assert "current_observation_time_invalid" not in report["global_reason_codes"]
        assert report["ready_count"] == 1
        future = service.scan(db, now=actual - timedelta(minutes=1))
        assert "current_observation_time_invalid" in future["global_reason_codes"]
        row = db.scalar(select(ValuationSnapshot).where(
            ValuationSnapshot.symbol == "600001.SH"
        ))
        row.raw_payload = {**row.raw_payload, "observation_mode": "same_evening"}
        db.commit()
        wrong = service.scan(db, now=actual)
        assert "valuation_observation_mode_unverified" in wrong["rows"][0]["reason_codes"]
        assert wrong["snapshot_hash"] != report["snapshot_hash"]
        assert not service._read_view(
            db, report, now=actual + timedelta(days=1),
        )["candidate_eligible"]
    engine.dispose()
