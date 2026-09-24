import json
from datetime import date

import pytest

from app.data_sources.eastmoney import (
    EastmoneyDataError,
    EastmoneyDataSource,
    normalize_symbol,
)


def test_current_valuation_pages_check_vendor_total_and_include_beijing(monkeypatch):
    source = EastmoneyDataSource()
    requests = []

    def get_json(host, path, params):
        requests.append(dict(params))
        if params["pn"] == 1:
            payload = {
                "data": {
                    "total": 101,
                    "diff": [{"f12": f"{600000 + index}"} for index in range(100)],
                }
            }
        else:
            payload = {"data": {"total": 101, "diff": [{"f12": "920992"}]}}
        return payload, json.dumps(payload)

    monkeypatch.setattr(source, "_get_json_with_raw", get_json)
    pages = list(source.iter_valuation_snapshot_pages())
    assert [(number, total, len(rows)) for number, total, rows, _ in pages] == [
        (1, 101, 100),
        (2, 101, 1),
    ]
    assert json.loads(pages[0][3])["data"]["total"] == 101
    assert "m:0+t:81+s:2048" in requests[0]["fs"]
    assert normalize_symbol(pages[1][2][0]["f12"]) == "920992.BJ"


def test_same_day_dated_valuation_requires_matching_vendor_date(monkeypatch):
    source = EastmoneyDataSource()
    target = date(2026, 9, 23)

    def page(*, trade_date, page, page_size):
        assert page == 1 and page_size == 500
        item = {
            "SECURITY_CODE": "920992", "SECUCODE": "920992.BJ",
            "TRADE_DATE": "2026-09-23 00:00:00", "CLOSE_PRICE": 10,
            "PE_TTM": 12, "PB_MRQ": 1.5, "BOARD_NAME": "工业",
        }
        return {"success": True, "result": {"count": 1, "data": [item]}}, "{}"

    monkeypatch.setattr(source, "fetch_historical_valuation_page", page)
    output = list(source.iter_same_day_dated_valuation_pages(trade_date=target))
    assert output[0][2][0]["_supplier_payload"]["BOARD_NAME"] == "工业"
    assert output[0][2][0]["f13"] == 0
    assert output[0][3] == "{}"

    with pytest.raises(EastmoneyDataError, match="wrong trade date"):
        list(source.iter_same_day_dated_valuation_pages(trade_date=date(2026, 9, 22)))
