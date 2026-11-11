from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.raw import ImportBatch, RawVendorSnapshot
from app.models.system import JobLog
from scripts.maintenance.backfill_special_treatment import backfill
from tests.test_price_value_screen import NOW, TARGET, _prep_database


def test_current_status_backfill_audits_and_preserves_existing(tmp_path):
    engine = _prep_database(tmp_path)
    with Session(engine) as db:
        db.execute(text(
            "UPDATE market.daily_bar SET special_treatment_status=NULL WHERE symbol='600001.SH'"
        ))
        db.execute(text("UPDATE market.security SET name='*ST测试' WHERE symbol='600001.SH'"))
        db.commit()
        dry = backfill(db, target=TARGET, observed_at=NOW, dry_run=True)
        assert dry["eligible_count"] == 1
        assert db.scalar(select(func.count()).select_from(ImportBatch)) == 0
        result = backfill(db, target=TARGET, observed_at=NOW)
        assert result["derived_counts"] == {"ST": 1}
        assert db.execute(text(
            "SELECT symbol,special_treatment_status FROM market.daily_bar ORDER BY symbol"
        )).all() == [("600001.SH", "ST"), ("600002.SH", "normal")]
        snapshot = db.scalar(select(RawVendorSnapshot))
        assert snapshot.raw_payload["previous_status"] is None
        assert snapshot.raw_payload["name"] == "*ST测试"
        assert snapshot.raw_payload["derived_status"] == "ST"
        assert snapshot.raw_payload["pit_verified"] is False
        assert db.scalar(select(JobLog.rows_updated).where(
            JobLog.job_name == "backfill_current_special_treatment"
        )) == 1
        assert backfill(db, target=TARGET, observed_at=NOW)["eligible_count"] == 0
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 1
        with pytest.raises(ValueError, match="latest completed"):
            backfill(db, target=TARGET - timedelta(days=1), observed_at=NOW)
    engine.dispose()
