from datetime import date
from decimal import Decimal

from app.config.settings import settings
from app.services import turnover_evidence as turnover_module
from app.services.turnover_evidence import TurnoverEvidenceService, reconcile_turnover


def test_turnover_reconciliation_rejects_price_conflicts_and_duplicates():
    trade_date = date(2026, 10, 26)
    rows = [
        {
            "ts_code": "600519.SH", "trade_date": "20261026",
            "close": "1400.00", "turnover_rate": "1.23",
        },
        {
            "ts_code": "000001.SZ", "trade_date": "20261026",
            "close": "11.55", "turnover_rate": "2.00",
        },
        {
            "ts_code": "600519.SH", "trade_date": "20261026",
            "close": "1400.00", "turnover_rate": "1.23",
        },
    ]
    bars = {
        ("600519.SH", trade_date): Decimal("1400.00"),
        ("000001.SZ", trade_date): Decimal("11.50"),
    }
    result = reconcile_turnover(rows, bars, source="tushare", trade_date=trade_date)
    assert result["matched_close_and_turnover"] == 1
    assert result["price_mismatch"] == 1
    assert result["invalid_or_duplicate"] == 1
    assert result["market_coverage_rate"] == "0.5"
    assert result["publication_scope"] == "raw_only"


def test_turnover_percentage_is_not_rescaled_or_filled():
    trade_date = date(2026, 10, 26)
    bars = {("600519.SH", trade_date): Decimal("1400")}
    rows = [
        {"ts_code": "600519.SH", "trade_date": "20261026", "close": "1400", "turnover_rate": None}
    ]
    result = reconcile_turnover(rows, bars, source="tushare", trade_date=trade_date)
    assert result["invalid_or_duplicate"] == 1
    assert result["matched_close_and_turnover"] == 0


def test_turnover_fallback_requires_reconciled_close(monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "test-secret")
    monkeypatch.setattr(turnover_module, "record_fallback_outcome",
                        lambda db, dataset, outcome: outcome)
    monkeypatch.setattr(turnover_module, "source_attempted_recently",
                        lambda db, dataset, source_name, within: False)
    service = TurnoverEvidenceService()
    calls = []

    def collect_day(db, *, source, trade_date, symbol):
        calls.append(source)
        return {"batch_no": source, "matched_close_and_turnover":
                0 if source == "tushare" else 1}

    monkeypatch.setattr(service, "collect_day", collect_day)
    result = service.collect_with_fallback(
        None, trade_date=date(2026, 10, 26), symbol="600519.SH"
    )
    assert result["selected_source"] == "akshare"
    assert result["publication_scope"] == "raw_only"
    assert [item["status"] for item in result["attempts"]] == [
        "unmatched", "matched"
    ]
    assert calls == ["tushare", "akshare"]


def test_turnover_fallback_stops_after_primary_match(monkeypatch):
    monkeypatch.setattr(settings, "tushare_token", "test-secret")
    monkeypatch.setattr(turnover_module, "record_fallback_outcome",
                        lambda db, dataset, outcome: outcome)
    monkeypatch.setattr(turnover_module, "source_attempted_recently",
                        lambda db, dataset, source_name, within: False)
    service = TurnoverEvidenceService()
    calls = []

    def collect_day(db, *, source, trade_date, symbol):
        calls.append(source)
        return {"batch_no": source, "matched_close_and_turnover": 1}

    monkeypatch.setattr(service, "collect_day", collect_day)
    result = service.collect_with_fallback(
        None, trade_date=date(2026, 10, 26), symbol="600519.SH"
    )
    assert result["selected_source"] == "tushare"
    assert calls == ["tushare"]
