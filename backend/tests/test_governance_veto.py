"""Tests for the report-layer governance veto overlay.

The veto overlay must never mutate the kernel result and must keep the
"passed every quantitative gate" fact separate from the "human rejected it"
fact.  These tests assert that contract directly.
"""

from __future__ import annotations

from datetime import date, datetime

import app.services.value_investing_screen as screen_module
from app.services.value_investing_screen import ValueInvestingScreenService
from app.strategies.governance_veto import (
    GOVERNANCE_VETOES,
    apply_governance_vetoes,
    veto_by_symbol,
)


def _result_with_shortlist() -> dict:
    return {
        "qualified_count": 1,
        "shortlist_count": 1,
        "deferred_count": 0,
        "shortlist": [
            {
                "symbol": "601799.SH",
                "name": "星宇股份",
                "status": "selected",
                "rank": 1,
                "metrics": {"close": "66.18"},
                "valuation": {"v_low": "110.69"},
            }
        ],
        "deferred": [],
        "qualified_symbols": ["601799.SH"],
    }


def test_veto_by_symbol_maps_declared_symbols() -> None:
    mapping = veto_by_symbol()
    assert set(mapping) == {veto.symbol for veto in GOVERNANCE_VETOES}
    assert "601799.SH" in mapping


def test_veto_record_carries_evidence() -> None:
    veto = veto_by_symbol()["601799.SH"]
    assert veto.name == "星宇股份"
    assert veto.reason
    assert veto.event
    assert len(veto.evidence) >= 1
    assert isinstance(veto.vetoed_at, date)
    payload = veto.to_dict()
    assert payload["vetoed_at"] == veto.vetoed_at.isoformat()
    assert payload["evidence"] == list(veto.evidence)


def test_apply_veto_moves_passing_name_out_of_active_shortlist() -> None:
    result = _result_with_shortlist()
    applied = apply_governance_vetoes(result, as_of_date=date(2026, 11, 16))

    # The kernel fact is preserved: the security still passed every gate.
    assert applied["qualified_count"] == 1
    assert applied["shortlist_count"] == 1
    assert applied["qualified_symbols"] == ["601799.SH"]

    # The human overlay removes it from the active list and records why.
    assert applied["active_shortlist"] == []
    assert applied["active_shortlist_count"] == 0
    assert applied["vetoed_count"] == 1
    assert len(applied["governance_vetoes"]) == 1
    vetoed = applied["governance_vetoes"][0]
    assert vetoed["symbol"] == "601799.SH"
    assert vetoed["governance_veto"]["name"] == "星宇股份"
    assert vetoed["governance_veto"]["reason"]


def test_apply_veto_does_not_mutate_input() -> None:
    result = _result_with_shortlist()
    snapshot = [dict(row) for row in result["shortlist"]]
    apply_governance_vetoes(result, as_of_date=date(2026, 11, 16))
    assert result["shortlist"] == snapshot
    assert "active_shortlist" not in result


def test_apply_veto_leaves_unrelated_shortlist_untouched() -> None:
    result = _result_with_shortlist()
    result["shortlist"] = [
        {
            "symbol": "600000.SH",
            "name": "浦发银行",
            "status": "selected",
            "rank": 1,
            "metrics": {},
            "valuation": {},
        }
    ]
    applied = apply_governance_vetoes(result, as_of_date=date(2026, 11, 16))
    assert applied["active_shortlist"] == result["shortlist"]
    assert applied["vetoed_count"] == 0
    assert applied["governance_vetoes"] == []


def test_historical_screen_does_not_consume_future_human_judgment() -> None:
    result = _result_with_shortlist()
    before = apply_governance_vetoes(result, as_of_date=date(2026, 11, 15))
    after = apply_governance_vetoes(result, as_of_date=date(2026, 11, 16))
    assert before["active_shortlist_count"] == 1
    assert before["vetoed_count"] == 0
    assert after["active_shortlist_count"] == 0
    assert after["vetoed_count"] == 1


def test_explicit_historical_service_report_keeps_veto_out(monkeypatch) -> None:
    class Connection:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ValueInvestingScreenService, "_connect", lambda self: Connection())
    monkeypatch.setattr(
        ValueInvestingScreenService,
        "_definition",
        lambda self, connection: ("value_investing_margin", "v3", None),
    )
    monkeypatch.setattr(
        ValueInvestingScreenService,
        "_target_date",
        lambda self, connection, *, as_of: as_of or date(2026, 11, 13),
    )
    monkeypatch.setattr(
        ValueInvestingScreenService,
        "_build_evidence",
        lambda self, connection, *, target, params: ([], {}),
    )
    monkeypatch.setattr(screen_module, "screen", lambda evidence, params: _result_with_shortlist())

    service = ValueInvestingScreenService.__new__(ValueInvestingScreenService)
    report = service.run(as_of=date(2026, 11, 15))
    assert report["governance_as_of"] == "2026-11-15"
    assert report["vetoed_count"] == 0
    assert report["active_shortlist_count"] == 1

    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 11, 16, tzinfo=tz)

    monkeypatch.setattr(screen_module, "datetime", Clock)
    current = service.run()
    assert current["target_trade_date"] == "2026-11-13"
    assert current["governance_as_of"] == "2026-11-16"
    assert current["vetoed_count"] == 1
    assert current["active_shortlist_count"] == 0
