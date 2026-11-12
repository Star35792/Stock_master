"""Integration guards for the Tencent valuation source.

These tests lock in the properties that were discovered by measurement and
that a later change could easily break:

1. The vendor quote can only describe the **latest completed session**.
   Asking it for an older date must be refused, not silently misdated.
2. The vendor's two prices are relative to its *quote day*, and the quote
   day rolls at 09:15.  Which field carries the observed close therefore
   depends on the moment of the run, and the choice has to be **checked**
   against the trusted close rather than assumed.
3. The rows this source publishes must stay **isolated** from the generic
   factor readers until the point-in-time consumption path is separated.
"""

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.data_sources.tencent import TencentQuoteDataSource
from app.database.base import Base
from app.database.session import SCHEMAS
from app.models.market import DailyBar, Security, TradingCalendar, ValuationSnapshot
from app.services.tencent_valuation_collection import (
    TencentValuationCollectionService,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")

#: The securities the fixture knows about.
_SECURITIES = {
    "600519.SH": ("贵州茅台", "SH"),
    "000001.SZ": ("平安银行", "SZ"),
    "300750.SZ": ("宁德时代", "SZ"),
}

#: The trusted closes of each session the fixture knows about.
_SESSIONS: dict[date, dict[str, Decimal]] = {
    date(2026, 11, 9): {
        "600519.SH": Decimal("1500.00"),
        "000001.SZ": Decimal("12.00"),
        "300750.SZ": Decimal("205.00"),
    },
    date(2026, 11, 10): {
        "600519.SH": Decimal("1439.00"),
        "000001.SZ": Decimal("11.86"),
        "300750.SZ": Decimal("198.00"),
    },
    date(2026, 11, 11): {
        "600519.SH": Decimal("1402.30"),
        "000001.SZ": Decimal("11.71"),
        "300750.SZ": Decimal("201.00"),
    },
    date(2026, 11, 12): {
        "600519.SH": Decimal("1410.00"),
        "000001.SZ": Decimal("11.60"),
        "300750.SZ": Decimal("199.00"),
    },
    date(2026, 11, 13): {
        "600519.SH": Decimal("1420.00"),
        "000001.SZ": Decimal("11.55"),
        "300750.SZ": Decimal("203.00"),
    },
}

#: Share counts used to build the vendor's market caps.
_SHARES = {"600519.SH": 1_250_000_000, "000001.SZ": 19_405_920_000, "300750.SZ": 4_400_000_000}


def _build_engine(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"duckdb:///{tmp_path / 'p1.duckdb'}")
    with engine.connect() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _seed(db: Session, sessions: dict[date, dict[str, Decimal]]) -> None:
    """Seed the calendar, the securities and the trusted bars of each session."""
    for symbol, (name, exchange) in _SECURITIES.items():
        db.add(
            Security(
                symbol=symbol,
                name=name,
                security_type="stock",
                exchange=exchange,
                currency="CNY",
                is_active=True,
                list_date=date(2001, 8, 27),
                source_name="test",
            )
        )
    for session_date, closes in sessions.items():
        for exchange in ("SH", "SZ", "BJ"):
            db.add(
                TradingCalendar(
                    exchange=exchange,
                    trade_date=session_date,
                    is_open=True,
                    source_name="test",
                )
            )
        for symbol, close in closes.items():
            db.add(
                DailyBar(
                    symbol=symbol,
                    trade_date=session_date,
                    adjust_type="none",
                    security_type="stock",
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("1000"),
                    amount=Decimal("1439000"),
                    quality_status="trusted",
                    is_suspect=False,
                    source_name="test",
                )
            )
    db.commit()


def _quote(price: float, last_close: float, *, shares: float) -> dict[str, object]:
    """Build a vendor payload for a stock with a known share count.

    The earnings per share (65) and the book value per share (100) are fixed,
    so the PE and the PB move with the price -- exactly how the vendor
    computes them.
    """
    return {
        "name": "x",
        "price": price,
        "last_close": last_close,
        "pe_ttm": round(price / 65.0, 4),
        "pe_static": round(price / 60.0, 4),
        "pb": round(price / 100.0, 4),
        "mcap_yi": round(shares * price / 1e8, 4),
        "float_mcap_yi": round(shares * price / 1e8, 4),
        "is_stale": False,
    }


def _vendor_quotes(
    quote_day: date,
    previous_day: date,
    *,
    overrides: dict[str, dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Build the vendor payload the endpoint would return on ``quote_day``.

    ``price`` carries the quote day's close and ``last_close`` the previous
    session's close -- the measured contract.
    """
    quotes: dict[str, dict[str, object]] = {}
    for symbol, shares in _SHARES.items():
        price = float(_SESSIONS[quote_day][symbol])
        last_close = float(_SESSIONS[previous_day][symbol])
        quotes[symbol.split(".")[0]] = _quote(price, last_close, shares=shares)
    for code, override in (overrides or {}).items():
        quotes[code] = {**quotes[code], **override}
    return quotes


class _FakeQuotes(TencentQuoteDataSource):
    """Stands in for the vendor quote endpoint.

    Only the network call is replaced; the symbol mapping, the stale-quote
    filter, the caliber restatement and the market-cap unit conversion stay
    the production code, so the test exercises the real normalisation path.
    """

    def __init__(self, payload: dict[str, dict[str, object]]) -> None:
        super().__init__()
        self.payload = payload
        self.requested: list[str] = []

    def fetch_quotes(self, codes: list[str]) -> dict[str, dict[str, object]]:
        self.requested.extend(codes)
        return {code: self.payload[code] for code in codes if code in self.payload}


def _service(fake: _FakeQuotes, clock: datetime) -> TencentValuationCollectionService:
    return TencentValuationCollectionService(
        data_source=fake,  # type: ignore[arg-type]
        clock=lambda: clock,
    )


# --------------------------------------------------------------------- target


def test_an_older_trade_date_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The quote carries the latest session's close, so older dates must fail.

    Measured: asking for 2026-10-28 while the quote held the 2026-11-10 close
    passed the price gate for 121 symbols, and every one of them carried the
    newer date's price.  A refusal is the only honest answer.
    """
    engine = _build_engine(tmp_path)
    with Session(engine) as db:
        _seed(db, {date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)]})
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(_FakeQuotes({}), clock)

        with pytest.raises(ValueError, match="latest completed session"):
            service.run(db, trade_date=date(2026, 10, 28))


def test_today_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A same-day quote is a half-day snapshot and must not be published."""
    engine = _build_engine(tmp_path)
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(_FakeQuotes({}), clock)

        with pytest.raises(ValueError, match="completed trading day"):
            service.run(db, trade_date=date(2026, 11, 11))


def test_the_observed_session_is_the_last_one_before_today(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A day is only observed once it has fully passed.

    The rule the operator set: at 21:00 the current day has not passed yet,
    so the previous session is observed; only after midnight does the
    service move on to the day that just ended.
    """
    engine = _build_engine(tmp_path)
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
                date(2026, 11, 12): _SESSIONS[date(2026, 11, 12)],
                date(2026, 11, 13): _SESSIONS[date(2026, 11, 13)],
            },
        )

        # Both runs happen on the same quote day: 11-12 is the session the
        # endpoint serves until it rolls at 09:15 on 11-13.
        quotes = _vendor_quotes(date(2026, 11, 12), date(2026, 11, 11))

        evening = _service(_FakeQuotes(quotes), datetime(2026, 11, 12, 21, 0, tzinfo=SHANGHAI))
        assert evening.run(db).trade_date == date(2026, 11, 11)

        after_midnight = _service(
            _FakeQuotes(quotes), datetime(2026, 11, 13, 0, 30, tzinfo=SHANGHAI)
        )
        assert after_midnight.run(db).trade_date == date(2026, 11, 12)


# ---------------------------------------------------------------------- field


def test_a_run_before_the_roll_reads_the_price_field(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Before 09:15 the endpoint still serves the previous session.

    This is the exact situation that produced the misdated batch on
    2026-11-12: a run at 09:03 read ``last_close`` and published the
    2026-11-10 close under the 2026-11-11 label.
    """
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(_vendor_quotes(date(2026, 11, 11), date(2026, 11, 10)))
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
                date(2026, 11, 12): _SESSIONS[date(2026, 11, 12)],
            },
        )
        clock = datetime(2026, 11, 12, 9, 3, tzinfo=SHANGHAI)

        result = _service(fake, clock).run(db)

        assert result.trade_date == date(2026, 11, 11)
        assert result.close_field == "price"

        rows = {row.symbol: row for row in db.scalars(select(ValuationSnapshot))}
        assert rows["600519.SH"].close == Decimal("1402.30")
        assert rows["000001.SZ"].close == Decimal("11.71")


def test_a_run_after_the_roll_reads_the_last_close_field(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """From 09:15 on, the endpoint has rolled and ``last_close`` is the target."""
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(_vendor_quotes(date(2026, 11, 11), date(2026, 11, 10)))
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)

        result = _service(fake, clock).run(db)

        assert result.trade_date == date(2026, 11, 10)
        assert result.close_field == "last_close"

        rows = {row.symbol: row for row in db.scalars(select(ValuationSnapshot))}
        assert rows["600519.SH"].close == Decimal("1439.00")
        assert rows["000001.SZ"].close == Decimal("11.86")


def test_the_selected_field_is_checked_against_the_trusted_close(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A field that does not describe the target aborts the run.

    The quote-day roll is the vendor's behaviour, not a guarantee.  If it
    ever changes, the run has to fail loudly instead of publishing rows
    dated to the wrong session.
    """
    engine = _build_engine(tmp_path)
    # The endpoint behaves as if it had already rolled: ``price`` holds the
    # older session's close, so the field the rule picks no longer matches.
    fake = _FakeQuotes(_vendor_quotes(date(2026, 11, 10), date(2026, 11, 9)))
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 9): _SESSIONS[date(2026, 11, 9)],
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 12, 9, 3, tzinfo=SHANGHAI)

        with pytest.raises(ValueError, match="does not describe"):
            _service(fake, clock).run(db)

        assert list(db.scalars(select(ValuationSnapshot))) == []


# ---------------------------------------------------------------- publication


def test_the_latest_session_is_published_and_isolated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A verified row is published and stays out of the generic readers."""
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(_vendor_quotes(date(2026, 11, 11), date(2026, 11, 10)))
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)

        result = _service(fake, clock).run(db, trade_date=date(2026, 11, 10))

        assert result.accepted == 3
        assert result.rejected == 0
        assert result.inserted == 3

        row = db.scalars(
            select(ValuationSnapshot).where(
                ValuationSnapshot.source_name == "tencent",
                ValuationSnapshot.symbol == "600519.SH",
            )
        ).one()
        assert row.symbol == "600519.SH"
        assert row.close == Decimal("1439.00")
        assert row.raw_payload is not None
        assert row.raw_payload["_close_field"] == "last_close"

        # The isolation marker is what keeps the generic factor readers away.
        assert row.raw_payload.get("_research_batch_id") is not None
        assert row.raw_payload.get("pit_verified") is False

    from app.repositories.market import ValuationSnapshotRepository

    with Session(engine) as db:
        available = ValuationSnapshotRepository().list_available(
            db, start_date=date(2026, 11, 1), end_date=date(2026, 11, 30)
        )
        assert available == []


def test_the_published_market_cap_matches_the_published_close(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The row's own numbers have to agree with each other.

    The vendor quotes the market cap against the quote day's price.  Taking
    the close from another session without restating the cap produces a row
    whose implied share count is wrong -- which is what the 2026-11-10 batch
    did for 5,561 symbols.
    """
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(_vendor_quotes(date(2026, 11, 11), date(2026, 11, 10)))
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)

        _service(fake, clock).run(db, trade_date=date(2026, 11, 10))

        rows = {row.symbol: row for row in db.scalars(select(ValuationSnapshot))}
        row = rows["600519.SH"]
        assert row.total_market_cap is not None
        implied_shares = row.total_market_cap / row.close
        assert abs(implied_shares - _SHARES["600519.SH"]) / _SHARES["600519.SH"] < (
            Decimal("0.0001")
        )
        # The earnings per share is fixed, so the PE follows the close.
        assert row.pe_ttm is not None
        assert abs(row.close / row.pe_ttm - Decimal("65")) < Decimal("0.01")


def test_a_price_mismatch_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A close that does not match the trusted bar is never published."""
    engine = _build_engine(tmp_path)
    quotes = _vendor_quotes(date(2026, 11, 11), date(2026, 11, 10))
    # One symbol quotes a price the trusted bar does not have.
    quotes["300750"] = _quote(199.00, 199.00, shares=_SHARES["300750.SZ"])
    fake = _FakeQuotes(quotes)
    with Session(engine) as db:
        _seed(
            db,
            {
                date(2026, 11, 10): _SESSIONS[date(2026, 11, 10)],
                date(2026, 11, 11): _SESSIONS[date(2026, 11, 11)],
            },
        )
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)

        result = _service(fake, clock).run(db, trade_date=date(2026, 11, 10))

        assert result.accepted == 2
        assert result.rejection_reasons.get("price_mismatch") == 1
        rows = {row.symbol for row in db.scalars(select(ValuationSnapshot))}
        assert "300750.SZ" not in rows
