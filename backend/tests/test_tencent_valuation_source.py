"""Tests for the Tencent quote adapter and the valuation collection gates."""

from datetime import date
from decimal import Decimal

import pytest

from app.data_sources.tencent import (
    BATCH_SIZE,
    TencentQuoteDataSource,
    _prefixed,
)


class TestSymbolPrefixing:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("600519", "600519.SH"),
            ("688981", "688981.SH"),
            ("000001", "000001.SZ"),
            ("002415", "002415.SZ"),
            ("300308", "300308.SZ"),
            ("920992", "920992.BJ"),
            ("830799", "830799.BJ"),
        ],
    )
    def test_known_code_segments(self, code: str, expected: str) -> None:
        assert _prefixed(code) == expected

    @pytest.mark.parametrize("code", ["60051", "6005199", "abcdef", ""])
    def test_malformed_codes_are_rejected(self, code: str) -> None:
        with pytest.raises(ValueError):
            _prefixed(code)


class TestBatchSize:
    def test_the_vendor_uri_limit_is_respected(self) -> None:
        # Measured: a single request for the whole market comes back as
        # HTTP 414 Request-URI Too Large.
        assert BATCH_SIZE == 60


class TestToValuationRecords:
    def _quotes(self) -> dict[str, dict[str, object]]:
        return {
            "600519": {
                "name": "贵州茅台",
                "price": 1402.30,
                "last_close": 1439.00,
                "pe_ttm": 21.34,
                "pe_static": 20.39,
                "pb": 6.46,
                "mcap_yi": 17529.89,
                "float_mcap_yi": 17529.89,
                "is_stale": False,
            },
            "000001": {
                "name": "平安银行",
                "price": 11.71,
                "last_close": 11.86,
                "pe_ttm": 5.23,
                "pb": 0.48,
                "mcap_yi": 2272.43,
                "float_mcap_yi": 2272.41,
                "is_stale": False,
            },
        }

    def test_market_caps_are_converted_from_hundred_million_yuan(self) -> None:
        source = TencentQuoteDataSource()
        records = source.to_valuation_records(
            self._quotes(),
            trade_date=date(2026, 11, 10),
            close_field="last_close",
        )
        by_symbol = {record.symbol: record for record in records}

        assert by_symbol["600519.SH"].total_market_cap == Decimal("1752989000000")
        assert by_symbol["600519.SH"].float_market_cap == Decimal("1752989000000")

    def test_the_close_comes_from_the_chosen_field(self) -> None:
        source = TencentQuoteDataSource()

        last_close_records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        price_records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="price"
        )

        last_close = {r.symbol: r.close for r in last_close_records}
        price = {r.symbol: r.close for r in price_records}
        assert last_close["600519.SH"] == Decimal("1439.00")
        assert price["600519.SH"] == Decimal("1402.30")

    def test_stale_quotes_are_dropped(self) -> None:
        quotes = self._quotes()
        quotes["600519"]["is_stale"] = True
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            quotes, trade_date=date(2026, 11, 10), close_field="last_close"
        )

        assert {record.symbol for record in records} == {"000001.SZ"}

    def test_symbols_outside_the_trusted_universe_are_dropped(self) -> None:
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            self._quotes(),
            trade_date=date(2026, 11, 10),
            close_field="last_close",
            allowed_symbols={"000001.SZ"},
        )

        assert {record.symbol for record in records} == {"000001.SZ"}

    def test_the_trailing_pe_is_kept_separately_from_the_static_one(self) -> None:
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        by_symbol = {record.symbol: record for record in records}

        assert by_symbol["600519.SH"].pe_ttm == Decimal("21.34")
        assert by_symbol["600519.SH"].pe_static == Decimal("20.39")
