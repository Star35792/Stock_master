"""Integration guards for the Tencent valuation source.

These tests lock in the two properties that were discovered by measurement and
that a later change could easily break:

1. The vendor quote can only describe the **latest completed session**.
   Asking it for an older date must be refused, not silently misdated.
2. The rows this source publishes must stay **isolated** from the generic
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


def _build_engine(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_engine(f"duckdb:///{tmp_path / 'p1.duckdb'}")
    with engine.connect() as conn:
        for schema in SCHEMAS:
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, session_date: date, close: Decimal) -> None:
    db.add(
        TradingCalendar(
            exchange="SH", trade_date=session_date, is_open=True, source_name="test"
        )
    )
    db.add(
        TradingCalendar(
            exchange="SZ", trade_date=session_date, is_open=True, source_name="test"
        )
    )
    db.add(
        TradingCalendar(
            exchange="BJ", trade_date=session_date, is_open=True, source_name="test"
        )
    )
    db.add(
        Security(
            symbol="600519.SH",
            name="贵州茅台",
            security_type="stock",
            exchange="SH",
            currency="CNY",
            is_active=True,
            list_date=date(2001, 8, 27),
            source_name="test",
        )
    )
    db.add(
        DailyBar(
            symbol="600519.SH",
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


class _FakeQuotes(TencentQuoteDataSource):
    """Stands in for the vendor quote endpoint.

    Only the network call is replaced; the symbol mapping, the stale-quote
    filter and the market-cap unit conversion stay the production code, so
    the test exercises the real normalisation path.
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


def test_an_older_trade_date_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The quote carries the latest session's close, so older dates must fail.

    Measured: asking for 2026-10-28 while the quote held the 2026-11-10 close
    passed the price gate for 121 symbols, and every one of them carried the
    newer date's price.  A refusal is the only honest answer.
    """
    engine = _build_engine(tmp_path)
    with Session(engine) as db:
        _seed(db, session_date=date(2026, 11, 10), close=Decimal("1439.00"))
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(_FakeQuotes({}), clock)

        with pytest.raises(ValueError, match="latest completed session"):
            service.run(db, trade_date=date(2026, 10, 28))


def test_the_latest_session_is_published_and_isolated(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A verified row is published and stays out of the generic readers."""
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(
        {
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
            }
        }
    )
    with Session(engine) as db:
        _seed(db, session_date=date(2026, 11, 10), close=Decimal("1439.00"))
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(fake, clock)

        result = service.run(db, trade_date=date(2026, 11, 10))

        assert result.accepted == 1
        assert result.rejected == 0
        assert result.inserted == 1

        row = db.scalars(
            select(ValuationSnapshot).where(ValuationSnapshot.source_name == "tencent")
        ).one()
        assert row.symbol == "600519.SH"
        assert row.close == Decimal("1439.00")
        assert row.pe_ttm == Decimal("21.34")
        assert row.pb == Decimal("6.46")
        assert row.total_market_cap == Decimal("1752989000000")

        # The isolation marker is what keeps the generic factor readers away.
        assert row.raw_payload is not None
        assert row.raw_payload.get("_research_batch_id") is not None
        assert row.raw_payload.get("pit_verified") is False

    from app.repositories.market import ValuationSnapshotRepository

    with Session(engine) as db:
        available = ValuationSnapshotRepository().list_available(
            db, start_date=date(2026, 11, 1), end_date=date(2026, 11, 30)
        )
        assert available == []


def test_a_price_mismatch_is_rejected(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A close that does not match the trusted bar is never published."""
    engine = _build_engine(tmp_path)
    fake = _FakeQuotes(
        {
            "600519": {
                "name": "贵州茅台",
                "price": 1402.30,
                "last_close": 1400.00,
                "pe_ttm": 21.34,
                "pb": 6.46,
                "mcap_yi": 17529.89,
                "float_mcap_yi": 17529.89,
                "is_stale": False,
            }
        }
    )
    with Session(engine) as db:
        _seed(db, session_date=date(2026, 11, 10), close=Decimal("1439.00"))
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(fake, clock)

        result = service.run(db, trade_date=date(2026, 11, 10))

        assert result.accepted == 0
        assert result.rejection_reasons.get("price_mismatch") == 1
        rows = list(db.scalars(select(ValuationSnapshot)))
        assert rows == []


def test_today_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A same-day quote is a half-day snapshot and must not be published."""
    engine = _build_engine(tmp_path)
    with Session(engine) as db:
        _seed(db, session_date=date(2026, 11, 11), close=Decimal("1400.00"))
        clock = datetime(2026, 11, 11, 21, 30, tzinfo=SHANGHAI)
        service = _service(_FakeQuotes({}), clock)

        with pytest.raises(ValueError, match="completed trading day"):
            service.run(db, trade_date=date(2026, 11, 11))
