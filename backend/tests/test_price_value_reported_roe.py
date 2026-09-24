import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.database.session import initialize_database
from app.models.raw import RawVendorSnapshot
from app.services.price_value_reported_roe import (
    DATASET,
    PriceValueReportedRoeService,
    recent_report_dates,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


class Vendor:
    name = "eastmoney"

    def __init__(self, target):
        self.target = target
        self.periods = recent_report_dates(target)

    def fetch_reported_roe_page(self, *, report_date, page, page_size):
        assert page == 1 and page_size == 500
        notice = (self.target - timedelta(days=2)).isoformat()
        disclosed = f"{notice} 18:00:00"
        symbol = "600001.SH" if report_date == self.periods[0] else "600002.SH"
        item = {
            "SECUCODE": symbol,
            "REPORTDATE": f"{report_date} 00:00:00",
            "NOTICE_DATE": f"{notice} 00:00:00",
            "EITIME": disclosed,
            "WEIGHTAVG_ROE": 14.5,
            "BOARD_NAME": "食品饮料",
        }
        payload = {"success": True, "result": {"count": 1, "pages": 1, "data": [item]}}
        return payload, json.dumps(payload)


def test_reported_roe_collects_current_observation_with_raw_lineage(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'roe.duckdb'}")
    initialize_database(engine)
    observed_at = datetime.now(SHANGHAI)
    target = observed_at.date()
    with Session(engine) as db:
        result = PriceValueReportedRoeService(source=Vendor(target)).collect_into_batch(
            db, batch_id=123, target=target, observed_at=observed_at
        )
        assert result.source_rows == 2
        assert len(result.raw_page_hashes) == 2
        assert set(result.observations) == {"600001.SH", "600002.SH"}
        assert set(result.period_observations) == {
            period.isoformat() for period in recent_report_dates(target)
        }
        assert result.observations["600001.SH"].ratio == Decimal("0.145")
        assert result.observations["600001.SH"].industry == "食品饮料"
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot).where(
            RawVendorSnapshot.dataset == DATASET
        )) == 2
    engine.dispose()


def test_reported_roe_cannot_publish_a_late_historical_capture(tmp_path):
    engine = create_engine(f"duckdb:///{tmp_path / 'late-roe.duckdb'}")
    initialize_database(engine)
    yesterday = datetime.now(SHANGHAI) - timedelta(days=1)
    with Session(engine) as db, pytest.raises(ValueError, match="target date"):
        PriceValueReportedRoeService(source=Vendor(yesterday.date())).collect_into_batch(
            db, batch_id=123, target=yesterday.date(), observed_at=yesterday
        )
    engine.dispose()


def test_empty_new_quarter_uses_two_earlier_disclosed_periods(tmp_path):
    target = datetime(2026, 10, 1, 21, tzinfo=SHANGHAI)

    class QuarterVendor(Vendor):
        def fetch_reported_roe_page(self, *, report_date, page, page_size):
            if report_date == datetime(2026, 9, 30).date():
                empty = {"success": True, "result": {"count": 0, "pages": 0, "data": []}}
                return empty, json.dumps(empty)
            symbol = (
                "600001.SH" if report_date == datetime(2026, 6, 30).date()
                else "600002.SH"
            )
            item = {
                "SECUCODE": symbol,
                "REPORTDATE": f"{report_date} 00:00:00",
                "NOTICE_DATE": "2026-08-30 00:00:00",
                "EITIME": "2026-08-30 18:00:00",
                "WEIGHTAVG_ROE": 14.5,
                "BOARD_NAME": "食品饮料",
            }
            payload = {"success": True, "result": {"count": 1, "pages": 1, "data": [item]}}
            return payload, json.dumps(payload)

    engine = create_engine(f"duckdb:///{tmp_path / 'new-quarter.duckdb'}")
    initialize_database(engine)
    service = PriceValueReportedRoeService(
        source=QuarterVendor(target.date()), clock=lambda: target,
    )
    with Session(engine) as db:
        result = service.collect_into_batch(
            db, batch_id=123, target=target.date(), observed_at=target,
        )
        assert result.report_dates == ["2026-06-30", "2026-03-31"]
        assert set(result.observations) == {"600001.SH", "600002.SH"}
        assert len(result.raw_page_hashes) == 3
    engine.dispose()


def test_reported_roe_stops_when_two_periods_have_same_securities(tmp_path):
    target = datetime(2026, 9, 21, 21, tzinfo=SHANGHAI)
    class SameSymbolsVendor(Vendor):
        def fetch_reported_roe_page(self, *, report_date, page, page_size):
            assert report_date in recent_report_dates(target.date(), count=2)
            item = {
                "SECUCODE": "600001.SH",
                "REPORTDATE": f"{report_date} 00:00:00",
                "NOTICE_DATE": "2026-08-30 00:00:00",
                "EITIME": "2026-08-30 18:00:00",
                "WEIGHTAVG_ROE": 14.5,
                "BOARD_NAME": "食品饮料",
            }
            payload = {"success": True, "result": {"count": 1, "pages": 1, "data": [item]}}
            return payload, json.dumps(payload)
    engine = create_engine(f"duckdb:///{tmp_path / 'same-securities.duckdb'}")
    initialize_database(engine)
    service = PriceValueReportedRoeService(
        source=SameSymbolsVendor(target.date()), clock=lambda: target,
    )
    with Session(engine) as db:
        result = service.collect_into_batch(
            db, batch_id=123, target=target.date(), observed_at=target,
        )
        assert result.report_dates == ["2026-06-30", "2026-03-31"]
        assert len(result.raw_page_hashes) == 2
        assert result.observations["600001.SH"].report_date == date(2026, 6, 30)
        assert set(result.period_observations["2026-06-30"]) == {"600001.SH"}
        assert set(result.period_observations["2026-03-31"]) == {"600001.SH"}
    engine.dispose()


def test_reported_roe_crossing_midnight_keeps_raw_but_rejects_result(tmp_path):
    target = datetime(2026, 9, 21, 23, 59, tzinfo=SHANGHAI)
    after = target + timedelta(minutes=2)
    times = iter((target, after))
    engine = create_engine(f"duckdb:///{tmp_path / 'roe-midnight.duckdb'}")
    initialize_database(engine)
    service = PriceValueReportedRoeService(
        source=Vendor(target.date()), clock=lambda: next(times),
    )
    with Session(engine) as db:
        with pytest.raises(ValueError, match="crossed its target date"):
            service.collect_into_batch(
                db, batch_id=123, target=target.date(), observed_at=target,
            )
        assert db.scalar(select(func.count()).select_from(RawVendorSnapshot)) == 1
    engine.dispose()
