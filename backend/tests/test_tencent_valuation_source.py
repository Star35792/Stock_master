"""Tests for the Tencent quote adapter and the valuation collection gates."""

from datetime import date, datetime, time
from decimal import Decimal

import pytest

from app.data_sources.tencent import (
    BATCH_SIZE,
    QUOTE_DAY_ROLL,
    TencentQuoteDataSource,
    _prefixed,
    close_field_for_quote_day,
    to_symbol,
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

    def test_to_symbol_returns_none_instead_of_raising(self) -> None:
        assert to_symbol("600519") == "600519.SH"
        assert to_symbol("abcdef") is None
        assert to_symbol("") is None


class TestBatchSize:
    def test_the_vendor_uri_limit_is_respected(self) -> None:
        # Measured: a single request for the whole market comes back as
        # HTTP 414 Request-URI Too Large.
        assert BATCH_SIZE == 60


class TestQuoteDayRoll:
    """The vendor's quote day rolls at 09:15, so the field depends on the hour.

    Measured on the same stock: at 09:03 ``price`` held the 11-11 close and
    ``last_close`` the 11-10 close; at 09:19 ``price`` was a live tick and
    ``last_close`` the 11-11 close.  Hard-coding either field misdates half
    of every day, silently.
    """

    def test_before_the_roll_the_quote_day_is_still_the_previous_session(self) -> None:
        now = datetime(2026, 11, 12, 9, 3)
        assert close_field_for_quote_day(now, today_is_trading_day=True) == "price"

    def test_after_the_roll_the_quote_day_is_the_current_session(self) -> None:
        now = datetime(2026, 11, 12, 9, 19)
        assert close_field_for_quote_day(now, today_is_trading_day=True) == "last_close"

    def test_the_roll_happens_at_0915(self) -> None:
        assert QUOTE_DAY_ROLL == time(9, 15)
        before = datetime(2026, 11, 12, 9, 14, 59)
        at = datetime(2026, 11, 12, 9, 15)
        assert close_field_for_quote_day(before, today_is_trading_day=True) == "price"
        assert close_field_for_quote_day(at, today_is_trading_day=True) == "last_close"

    def test_a_non_trading_day_never_rolls(self) -> None:
        # Saturday: the endpoint keeps serving Friday's session all day.
        saturday = datetime(2026, 11, 14, 21, 30)
        assert close_field_for_quote_day(saturday, today_is_trading_day=False) == "price"

    def test_a_timezone_aware_moment_is_accepted(self) -> None:
        from zoneinfo import ZoneInfo

        shanghai = ZoneInfo("Asia/Shanghai")
        now = datetime(2026, 11, 12, 9, 3, tzinfo=shanghai)
        assert close_field_for_quote_day(now, today_is_trading_day=True) == "price"


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
            close_field="price",
        )
        by_symbol = {record.symbol: record for record in records}

        # On the ``price`` basis the vendor's own numbers are already the
        # observed session's, so nothing is rescaled.
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

    def test_the_market_cap_is_restated_onto_the_selected_close(self) -> None:
        """A cap quoted against the quote day cannot be published next to another
        session's close -- the row's own numbers would disagree with each other.

        The vendor quotes the cap against ``price``; selecting ``last_close``
        has to move it, or the row mixes two sessions.
        """
        source = TencentQuoteDataSource()
        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        record = next(r for r in records if r.symbol == "600519.SH")

        vendor_cap = Decimal("1752989000000")
        assert record.total_market_cap is not None
        assert record.total_market_cap != vendor_cap
        # 1439.00 > 1402.30, so the restated cap is the larger one.
        assert record.total_market_cap > vendor_cap

    def test_the_restated_market_cap_implies_the_same_share_count(self) -> None:
        """Restating moves the cap, never the share count.

        This is the property that proves the rescale is right: the vendor's
        own numbers recover the true share count, and so must the published
        ones.
        """
        source = TencentQuoteDataSource()
        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        record = next(r for r in records if r.symbol == "600519.SH")

        vendor_shares = Decimal("1752989000000") / Decimal("1402.30")
        published_shares = record.total_market_cap / record.close
        assert abs(published_shares - vendor_shares) / vendor_shares < Decimal("0.000001")

    def test_the_ratios_are_restated_with_the_market_cap(self) -> None:
        """The earnings per share is fixed, so the PE follows the close."""
        source = TencentQuoteDataSource()
        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        record = next(r for r in records if r.symbol == "600519.SH")

        vendor_eps = Decimal("1402.30") / Decimal("21.34")
        published_eps = record.close / record.pe_ttm
        assert abs(published_eps - vendor_eps) / vendor_eps < Decimal("0.000001")

    def test_the_price_basis_is_recorded_for_audit(self) -> None:
        source = TencentQuoteDataSource()
        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="last_close"
        )
        record = next(r for r in records if r.symbol == "600519.SH")

        assert record.raw_payload is not None
        assert record.raw_payload["_close_field"] == "last_close"
        assert Decimal(str(record.raw_payload["_quote_day_price"])) == Decimal("1402.30")
        assert record.raw_payload["_caliber"] == "restated_to_close"

    def test_a_stale_quote_is_dropped_on_the_price_basis(self) -> None:
        """On the ``price`` basis the quote day *is* the observed session.

        A frozen quote then means the session had no trade at all, so there
        is nothing to observe.
        """
        quotes = self._quotes()
        quotes["600519"]["is_stale"] = True
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            quotes, trade_date=date(2026, 11, 10), close_field="price"
        )

        assert {record.symbol for record in records} == {"000001.SZ"}

    def test_a_stale_quote_is_kept_on_the_last_close_basis(self) -> None:
        """On the ``last_close`` basis the previous session's close is real.

        The quote being frozen only says the *quote day* had no trade, which
        is the current session -- not the one being published.
        """
        quotes = self._quotes()
        # A genuinely frozen quote: no trade on the quote day, so the price
        # did not move from the previous close.
        quotes["600519"]["price"] = 1439.00
        quotes["600519"]["is_stale"] = True
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            quotes, trade_date=date(2026, 11, 10), close_field="last_close"
        )

        assert {record.symbol for record in records} == {"600519.SH", "000001.SZ"}
        # The price did not move, so the restatement factor is exactly one.
        record = next(r for r in records if r.symbol == "600519.SH")
        assert record.close == Decimal("1439.00")
        assert record.total_market_cap == Decimal("1752989000000")

    def test_symbols_outside_the_trusted_universe_are_dropped(self) -> None:
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            self._quotes(),
            trade_date=date(2026, 11, 10),
            close_field="last_close",
            allowed_symbols={"000001.SZ"},
        )

        assert {record.symbol for record in records} == {"000001.SZ"}

    def test_a_quote_without_a_price_basis_is_dropped(self) -> None:
        """Without the quote day's price the cap cannot be placed on the close."""
        quotes = self._quotes()
        quotes["600519"]["price"] = 0.0
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            quotes, trade_date=date(2026, 11, 10), close_field="last_close"
        )

        assert {record.symbol for record in records} == {"000001.SZ"}

    def test_the_trailing_pe_is_kept_separately_from_the_static_one(self) -> None:
        source = TencentQuoteDataSource()

        records = source.to_valuation_records(
            self._quotes(), trade_date=date(2026, 11, 10), close_field="price"
        )
        by_symbol = {record.symbol: record for record in records}

        assert by_symbol["600519.SH"].pe_ttm == Decimal("21.34")
        assert by_symbol["600519.SH"].pe_static == Decimal("20.39")
