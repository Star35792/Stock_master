import json
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from app.services.price_value_tracking import (
    VERSION,
    PriceValueForwardTrackingService,
    build_cohort,
)


def _report():
    return {
        "status": "partial", "global_reason_codes": [],
        "snapshot_hash": "abc123", "screen_version": "relative_value_research_v7",
        "selection_version": "value_research_shortlist_v2",
        "target_trade_date": "2026-10-28",
        "observation_mode": "current_research_catchup",
        "analysis_at": "2026-10-29T16:00:00+08:00",
        "generated_at": "2026-10-29T16:00:00+08:00",
        "selection": {"rule_impact": {"reason_counts": {}}},
        "rows": [
            {
                "symbol": "600001.SH",
                "shortlist": {"selected": True, "qualified": True},
                "research_diagnostics": {"review_eligible": True},
            },
            {
                "symbol": "600002.SH",
                "shortlist": {"selected": False, "qualified": True},
                "research_diagnostics": {"review_eligible": True},
            },
            {
                "symbol": "600003.SH",
                "shortlist": {"selected": False, "qualified": False},
                "research_diagnostics": {"review_eligible": True},
            },
        ],
    }


class _ScreenRepository:
    def __init__(self, cohort_name):
        self.cohort_name = cohort_name

    def latest_screen_job(self, db):
        return SimpleNamespace(metadata_={"cohort_name": self.cohort_name})


class _TrackingRepository:
    def __init__(self, sessions, prices):
        self.sessions = sessions
        self.prices = prices
        self.after = None

    def common_open_dates(self, db, *, after, limit):
        self.after = after
        return self.sessions[:limit]

    def qfq_closes(self, *, start_date, end_date, symbols):
        return {"rows": self.prices, "dataset": "fixture-qfq", "input_hash": "prices"}


def test_cohort_membership_is_frozen_from_report_rows():
    cohort = build_cohort(_report())
    assert cohort["version"] == VERSION
    assert cohort["pools"] == {
        "shortlist": ["600001.SH"],
        "qualified": ["600001.SH", "600002.SH"],
        "research": ["600001.SH", "600002.SH", "600003.SH"],
    }


def test_catchup_tracking_starts_after_actual_analysis_day_and_keeps_missing_members(tmp_path):
    cohort = build_cohort(_report())
    name = "fixture.cohort.json"
    (tmp_path / name).write_text(json.dumps(cohort), encoding="utf-8")
    sessions = [date(2026, 10, 30) + timedelta(days=offset) for offset in range(6)]
    prices = {}
    for symbol, end in (("600001.SH", "11"), ("600002.SH", "9")):
        prices[(symbol, sessions[0])] = Decimal("10")
        prices[(symbol, sessions[5])] = Decimal(end)
    repository = _TrackingRepository(sessions, prices)
    service = PriceValueForwardTrackingService(
        output_dir=tmp_path, screen_repository=_ScreenRepository(name),
        tracking_repository=repository,
    )
    result = service.latest(object())
    assert repository.after == date(2026, 10, 29)
    assert result["entry_date"] == "2026-10-30"
    assert result["observation_mode"] == "current_research_catchup"
    five = result["windows"][0]
    assert five["trading_days"] == 5
    assert five["pools"]["shortlist"]["gross_return_observed"] == "0.1"
    assert five["pools"]["qualified"]["gross_return_observed"] == "0.0"
    assert five["pools"]["research"]["member_count"] == 3
    assert five["pools"]["research"]["observed_count"] == 2
    assert five["pools"]["research"]["status"] == "incomplete"
    assert five["pools"]["research"]["missing_reason_counts"] == {
        "entry_unobservable": 1,
    }
    assert result["windows"][1]["status"] == "pending"
    assert result["windows"][2]["status"] == "pending"


def test_no_future_session_is_pending_instead_of_zero_return(tmp_path):
    cohort = build_cohort(_report())
    name = "fixture.cohort.json"
    (tmp_path / name).write_text(json.dumps(cohort), encoding="utf-8")
    service = PriceValueForwardTrackingService(
        output_dir=tmp_path, screen_repository=_ScreenRepository(name),
        tracking_repository=_TrackingRepository([], {}),
    )
    result = service.latest(object())
    assert result["status"] == "pending"
    assert result["entry_date"] is None
    assert result["windows"] == []


def test_blocked_report_cohort_is_never_observed(tmp_path):
    report = _report()
    report["status"] = "blocked"
    report["global_reason_codes"] = ["trusted_bars_missing_for_target"]
    cohort = build_cohort(report)
    name = "blocked.cohort.json"
    (tmp_path / name).write_text(json.dumps(cohort), encoding="utf-8")
    repository = _TrackingRepository([date(2026, 10, 30)], {})
    service = PriceValueForwardTrackingService(
        output_dir=tmp_path, screen_repository=_ScreenRepository(name),
        tracking_repository=repository,
    )

    result = service.latest(object())

    assert result["status"] == "blocked"
    assert result["reason_codes"] == ["frozen_cohort_report_blocked"]
    assert result["windows"] == []
    assert repository.after is None
