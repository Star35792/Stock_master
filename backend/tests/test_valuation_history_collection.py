"""Regression tests for the valuation-history collection service.

The sweep had no unit coverage, and that hid two defects that would have
silently corrupted the published dataset:

* **Pagination order.**  Sorting the supplier's paginated table by
  ``TRADE_DATE`` alone leaves the order *inside* a trading day up to the
  server, so consecutive pages overlap and drop rows: a measured sweep of
  eight days returned 571 duplicated keys between pages 1 and 2 and lost the
  same number of rows.  The composite sort ``TRADE_DATE,SECURITY_CODE`` with
  ``sortTypes=1,1`` makes the page boundaries deterministic.
* **Row-level price agreement.**  The gate compares the supplier's close
  against the trusted daily bar and rejects the trading day on a mismatch.
  A gate that silently accepted a mismatch would publish a valuation series
  that disagrees with the price series the research path uses.

These tests use a stub supplier so they assert the *rules*, not the
supplier's behaviour.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from app.data_sources.eastmoney import EastmoneyDataSource
from app.services.valuation_history_collection import (
    COLUMNS,
    UNIVERSE_COVERAGE_FLOOR,
    ValuationHistoryCollectionError,
    ValuationHistoryCollector,
)

_START = date(2026, 1, 5)
_END = date(2026, 1, 6)


def _row(symbol: str, trade_date: date, close: str) -> dict[str, object]:
    return {
        "SECURITY_CODE": symbol.split(".")[0],
        "SECUCODE": symbol,
        "TRADE_DATE": f"{trade_date.isoformat()} 00:00:00",
        "CLOSE_PRICE": close,
        "PE_TTM": 12.5,
        "PB_MRQ": 1.5,
        "PS_TTM": 2.5,
        "TOTAL_MARKET_CAP": 1000000,
        "TOTAL_SHARES": 100000,
        "FREE_SHARES_A": 80000,
        "NOTLIMITED_MARKETCAP_A": 900000,
        "CHANGE_RATE": 1.25,
    }


class _StubSource:
    """A supplier stub that serves a fixed page layout.

    The stub reports the same pagination metadata a real supplier would:
    ``count`` is the total number of rows across all pages and ``pages`` is
    the number of pages.  The collector validates both against the page
    content, so a stub that lied about them would test nothing.
    """

    def __init__(self, pages: dict[int, list[dict[str, object]]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, object]] = []

    def fetch_valuation_history_page(
        self, *, start_date: date, end_date: date, page: int, page_size: int
    ) -> tuple[dict, str]:
        self.calls.append(
            {
                "start_date": start_date,
                "end_date": end_date,
                "page": page,
                "page_size": page_size,
            }
        )
        items = self.pages.get(page, [])
        total = sum(len(rows) for rows in self.pages.values())
        return (
            {
                "success": True,
                "result": {
                    "count": total,
                    "pages": len(self.pages),
                    "data": items,
                },
            },
            f"page-{page}",
        )


def _collector(pages: dict[int, list[dict[str, object]]]) -> ValuationHistoryCollector:
    return ValuationHistoryCollector(source=_StubSource(pages), concurrency=2)


def test_build_rows_uses_the_supplier_symbol() -> None:
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "000001.SZ"
    assert rows[0]["close"] == Decimal("10.1234")
    assert rows[0]["pit_verified"] is False
    assert rows[0]["observed_at"] == "2026-01-07T00:00:00+00:00"


def test_build_rows_rejects_a_mismatched_secucode() -> None:
    collector = _collector({})
    bad = _row("000001.SZ", _START, "10.1234")
    bad["SECUCODE"] = "000002.SZ"
    with pytest.raises(ValueError, match="invalid valuation history security code"):
        collector.build_rows([bad], observed_at="2026-01-07T00:00:00+00:00")


def test_build_rows_keeps_a_negative_pe() -> None:
    """A negative PE is a real state (loss-making company), not bad data."""
    collector = _collector({})
    item = _row("000001.SZ", _START, "10.1234")
    item["PE_TTM"] = -8.25
    rows = collector.build_rows([item], observed_at="2026-01-07T00:00:00+00:00")
    assert rows[0]["pe_ttm"] == Decimal("-8.25")


@pytest.fixture
def small_pages(monkeypatch) -> None:
    """Shrink the page size so a multi-page sweep can be tested.

    The collector validates each page against the pagination metadata, using
    the module-level page size.  Shrinking it keeps the stub honest while
    letting a test serve several pages without building 5,000-row pages.
    """
    monkeypatch.setattr(
        "app.services.valuation_history_collection.PAGE_SIZE", 1
    )


def test_iter_pages_yields_in_page_order(small_pages: None) -> None:
    pages = {
        1: [_row("000001.SZ", _START, "10.0000")],
        2: [_row("000002.SZ", _START, "20.0000")],
        3: [_row("000003.SZ", _END, "30.0000")],
    }
    collector = _collector(pages)
    seen = [
        page
        for page, _items, _raw in collector.iter_pages(
            start_date=_START, end_date=_END
        )
    ]
    assert seen == [1, 2, 3]


def test_iter_pages_skips_the_staged_pages(small_pages: None) -> None:
    pages = {
        1: [_row("000001.SZ", _START, "10.0000")],
        2: [_row("000002.SZ", _START, "20.0000")],
    }
    collector = _collector(pages)
    seen = [
        page
        for page, _items, _raw in collector.iter_pages(
            start_date=_START, end_date=_END, skip_pages=frozenset({1})
        )
    ]
    assert seen == [2]


def test_day_gate_accepts_an_exact_price_match() -> None:
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    gate = collector.evaluate_day_gate(
        rows, trusted_closes={"000001.SZ": Decimal("10.1234")}
    )
    assert gate.accepted is True
    assert gate.price_mismatch_count == 0
    assert gate.universe_coverage == Decimal("1")


def test_day_gate_rejects_a_price_mismatch() -> None:
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    gate = collector.evaluate_day_gate(
        rows, trusted_closes={"000001.SZ": Decimal("10.9999")}
    )
    assert gate.accepted is False
    assert gate.price_mismatch_count == 1
    assert "price mismatch" in gate.reason


def test_day_gate_rejects_a_thin_universe() -> None:
    """A day that covers too little of the trusted universe is not evidence."""
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    trusted = {
        "000001.SZ": Decimal("10.1234"),
        "000002.SZ": Decimal("20.0000"),
        "000003.SZ": Decimal("30.0000"),
    }
    gate = collector.evaluate_day_gate(rows, trusted_closes=trusted)
    assert gate.accepted is False
    assert gate.universe_coverage < UNIVERSE_COVERAGE_FLOOR


def test_write_parquet_keeps_the_decimal_values(tmp_path: Path) -> None:
    """The write must not round-trip the values through a binary float."""
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    rows[0]["pe_ttm"] = Decimal("150.9739674100")
    rows[0]["total_market_cap"] = Decimal("5003482613.58")
    target = collector.write_parquet(rows, output_dir=tmp_path / "page")
    con = duckdb.connect(":memory:")
    try:
        record = con.execute(
            "SELECT symbol, trade_date, close, pe_ttm, total_market_cap, "
            "pit_verified FROM read_parquet(?)",
            [target.as_posix()],
        ).fetchone()
    finally:
        con.close()
    assert record[0] == "000001.SZ"
    assert record[1] == _START
    assert record[2] == Decimal("10.1234")
    assert record[3] == Decimal("150.9739674100")
    assert record[4] == Decimal("5003482613.58")
    assert record[5] is False


def test_write_parquet_leaves_no_temporary_file(tmp_path: Path) -> None:
    """The page directory must hold the Parquet file and nothing else.

    A temporary CSV would have to be deleted again, and a sweep deletes
    hundreds of files, which trips the host environment's safe-delete guard.
    """
    collector = _collector({})
    rows = collector.build_rows(
        [_row("000001.SZ", _START, "10.1234")], observed_at="2026-01-07T00:00:00+00:00"
    )
    page_dir = tmp_path / "page"
    collector.write_parquet(rows, output_dir=page_dir)
    assert [path.name for path in page_dir.iterdir()] == ["data_0.parquet"]


def test_publish_accepted_pages_filters_by_date(tmp_path: Path) -> None:
    collector = _collector({})
    pages_dir = tmp_path / "_pages"
    for page, trade_date, symbol in (
        (1, _START, "000001.SZ"),
        (2, _END, "000002.SZ"),
    ):
        rows = collector.build_rows(
            [_row(symbol, trade_date, "10.1234")],
            observed_at="2026-01-07T00:00:00+00:00",
        )
        collector.write_parquet(rows, output_dir=pages_dir / f"page_{page:05d}")
    target = collector.publish_accepted_pages(
        pages_dir=pages_dir, accepted_dates=[_START], output_dir=tmp_path / "out"
    )
    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            "SELECT trade_date, count(*) FROM read_parquet(?) GROUP BY 1",
            [target.as_posix()],
        ).fetchall()
    finally:
        con.close()
    assert rows == [(_START, 1)]


def test_column_contract_matches_the_parquet_writer() -> None:
    """The declared column order is part of the on-disk contract."""
    assert COLUMNS[:4] == ("symbol", "trade_date", "close", "pe_ttm")
    assert "pit_verified" in COLUMNS
    assert COLUMNS[-2:] == ("source_name", "observed_at")


def test_supplier_request_uses_the_composite_sort(monkeypatch) -> None:
    """The supplier request must carry the deterministic composite sort.

    Sorting by ``TRADE_DATE`` alone leaves the order inside a trading day up
    to the server, so consecutive pages overlap and drop rows.  The request
    parameters are asserted here so the fix cannot silently regress.
    """
    captured: dict[str, object] = {}

    def _capture(self, host: str, path: str, params: dict[str, object]):
        captured.update(params)
        return {
            "success": True,
            "result": {"count": 1, "pages": 1, "data": [_row("000001.SZ", _START, "1.0")]},
        }, "{}"

    monkeypatch.setattr(
        "app.data_sources.eastmoney.EastmoneyDataSource._get_json_with_raw",
        _capture,
    )
    source = EastmoneyDataSource()
    source.fetch_valuation_history_page(
        start_date=_START, end_date=_END, page=1, page_size=5000
    )
    assert captured["sortColumns"] == "TRADE_DATE,SECURITY_CODE"
    assert captured["sortTypes"] == "1,1"


class _ThrottlingSource:
    """A supplier that rejects the first attempts with a throttling payload."""

    def __init__(self, *, reject_times: int) -> None:
        self.reject_times = reject_times
        self.attempts = 0

    def fetch_valuation_history_page(
        self, *, start_date: date, end_date: date, page: int, page_size: int
    ) -> tuple[dict, str]:
        self.attempts += 1
        if self.attempts <= self.reject_times:
            # The supplier throttles a burst of concurrent requests by
            # answering with an error payload instead of failing the
            # connection, so the transport-level retry does not help.
            return (
                {
                    "success": False,
                    "result": None,
                    "message": "too many requests",
                },
                "{}",
            )
        return (
            {
                "success": True,
                "result": {
                    "count": 1,
                    "pages": 1,
                    "data": [_row("000001.SZ", _START, "10.0000")],
                },
            },
            "{}",
        )


def test_page_fetch_retries_a_throttled_page(monkeypatch) -> None:
    """A business rejection must be retried, not abort the sweep."""
    monkeypatch.setattr(
        "app.services.valuation_history_collection.sleep", lambda _seconds: None
    )
    source = _ThrottlingSource(reject_times=2)
    collector = ValuationHistoryCollector(source=source, concurrency=1, page_retries=4)
    page, items, _raw, count, pages = collector._fetch_page(
        start_date=_START, end_date=_END, page=1
    )
    assert page == 1
    assert len(items) == 1
    assert source.attempts == 3


def test_page_fetch_gives_up_after_the_retry_budget(monkeypatch) -> None:
    """A permanently failing page must raise rather than return partial data."""
    monkeypatch.setattr(
        "app.services.valuation_history_collection.sleep", lambda _seconds: None
    )
    source = _ThrottlingSource(reject_times=99)
    collector = ValuationHistoryCollector(source=source, concurrency=1, page_retries=2)
    with pytest.raises(
        ValuationHistoryCollectionError, match="failed after 3 attempts"
    ):
        collector._fetch_page(start_date=_START, end_date=_END, page=7)
