"""Collect Tencent valuation observations and publish the verified ones.

Why a second valuation source
------------------------------
``market.valuation_snapshot`` had a single vendor and no ``pe_ttm`` at all:
the Eastmoney clist field the project uses is a *static* PE whose reporting
period the supplier does not certify, so the ingestion policy keeps it out of
the generic ``pe_ttm`` column.  With one vendor there is also nothing to
cross-check the vendor against.

This service adds Tencent as an independent second source.  It publishes a
genuine trailing PE and gives the valuation layer a second opinion on PB and
market capitalisation.

Publication gates
-----------------
The project's ingestion policy forbids writing unverified vendor rows into the
trusted layer, so every row has to pass all of the following before it is
published:

1. The target must be a completed mainland trading session (all three
   exchanges open) that is **strictly before today** in Shanghai time.  A
   quote taken during the session is a half-day snapshot, and today's bar is
   exactly that.
2. The vendor's price must match the **same-day trusted unadjusted close**
   within 0.01 yuan *and* within 0.2% relative.  A price that does not match
   means the vendor is reporting a different day, so the row is rejected.
   The relative half of the gate is what makes this work: a single tick is
   0.44% on a 2.27-yuan stock, and the absolute tolerance alone let a whole
   wrong session through.
3. The symbol must exist in the trusted security table as an active CNY
   stock on SH/SZ/BJ.

Which session is observed
-------------------------
The observed session is the last trading session **strictly before the
current Shanghai calendar day**.  A day is only observed once it has fully
passed, so a run at 21:00 on 2026-11-12 still observes 2026-11-11, and only
after midnight does the service move on to 2026-11-12.  The rule is
deliberately conservative: it makes an observation immutable once published
and removes any chance of publishing a half-day snapshot.

Which vendor field carries the close
-------------------------------------
The Tencent quote endpoint publishes two prices, and both are relative to the
vendor's *quote day*: ``price`` is the quote day's price (frozen close before
the session, live tick during it) and ``last_close`` is the close of the
session before the quote day.  The quote day rolls at **09:15**, when the
opening auction starts -- not at midnight.

That makes the choice of field depend on the moment the endpoint is called:

* before 09:15 on a trading day, and on every non-trading day, the endpoint
  still serves the previous session, so ``price`` is the observed session's
  close;
* from 09:15 on a trading day the endpoint has rolled to the current session,
  so ``last_close`` is the observed session's close.

Hard-coding one of the two is wrong for half of every day, and the failure is
silent: the wrong field still returns a plausible price for every symbol, it
just belongs to a different session.  Measured on 2026-11-12, a run that took
``last_close`` at 09:03 -- before the roll -- published 435 rows dated
2026-11-11 of which **434 carried the 2026-11-10 close**.  The price gate
rejected the other 5,138 rows, so the mistake surfaced as "coverage is bad"
rather than "the dates are wrong".

So this service *measures* the field instead of assuming it: it compares both
fields against the trusted close of the target session and refuses to publish
unless the field it selected actually agrees.  A rule that stops matching the
vendor's behaviour fails loudly instead of writing misdated rows.

The market cap and the ratios are restated onto the selected close.  The
vendor quotes them against the quote day's price, so copying them verbatim
next to a close from another session produces a row whose own numbers
disagree with each other -- exactly what the 2026-11-10 batch did.

The source can only describe **one** trade date
------------------------------------------------
This is the limit that matters, and it is structural rather than a matter of
tolerance.  ``last_close`` is the close of the most recent session, period.
Asking the vendor for an older date does not make it return that date: the
quote still carries the latest session's close, and no tolerance can tell the
difference when the two happen to be close.

Measured on 2026-10-28, the gate above accepted 121 symbols, and **every one
of the 121 carried the 2026-11-10 close**, not the 2026-10-28 close.  They
slipped through because those stocks barely moved over the two weeks.  A
tighter tolerance would only shrink the leak, not close it.

So the service pins the target to the session the source can actually
describe: the latest completed trading session before the observation moment.
A caller asking for any other date is told so instead of being handed
misdated rows.

Gate 2 rejects two classes of symbol, and both rejections are correct:

* **Index-code collisions** -- 108 six-digit codes are index codes on the
  trusted side while the vendor quotes a company under the same digits.
* **Ex-dividend symbols** -- for an ``XD`` symbol the vendor's
  ``last_close`` is the post-ex-rights price while the trusted bar holds the
  pre-ex-rights close.

Rows that fail any gate are counted and reported, never published.  The raw
vendor response is written first, so a rejected run still leaves evidence.

Isolation from the generic factor readers
-----------------------------------------
The rows this service publishes carry ``_research_batch_id`` in their
``raw_payload``, exactly like the Eastmoney same-evening observations.
``ValuationSnapshotRepository.list_available`` skips those rows, so the
generic market-cap and valuation factor readers keep ignoring them until the
point-in-time consumption path is separated.  Publishing them under
``source_name='tencent'`` keeps them addressable for cross-source
reconciliation without letting them leak into the factor pipeline.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from json import dumps
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config.settings import settings
from app.data_sources.tencent import (
    CloseField,
    TencentQuoteDataSource,
    close_field_for_quote_day,
    to_symbol,
)
from app.models.raw import ImportBatch, RawVendorSnapshot
from app.repositories.market import ValuationSnapshotRepository
from app.repositories.raw import RawDailyBarImportRepository
from app.services.job_logging import logged_job

SHANGHAI = ZoneInfo("Asia/Shanghai")
DATASET = "tencent_valuation_observation"

#: Maximum accepted price difference against the trusted close, in yuan.
#: A-share prices tick in 0.01, and the two vendors round differently.
PRICE_MATCH_TOLERANCE = Decimal("0.01")

#: Maximum accepted *relative* price difference against the trusted close.
#:
#: The absolute tolerance alone is not enough.  A 0.01 yuan difference is a
#: 0.44% difference on a 2.27-yuan stock, and that is wide enough to let a
#: quote from the *wrong* session through.  This was measured: asking the
#: vendor for 2026-10-28 while the quote carried the 2026-11-10 close passed
#: the absolute gate for 196 symbols, every one of them a low-priced stock
#: whose 11-10 price happened to sit within one tick of its 10-28 price.
#:
#: The two-threshold rule keeps a genuine rounding difference acceptable
#: while rejecting a whole session that drifted.
PRICE_MATCH_REL_TOLERANCE = Decimal("0.002")

#: Minimum share of the trusted universe in which the selected vendor field
#: has to agree with the trusted close before anything is published.
#:
#: The field is chosen from the vendor's documented quote-day roll, and this
#: threshold turns that rule into a checked assumption: if the vendor ever
#: changes when it rolls, the run fails loudly instead of publishing rows
#: dated to the wrong session.  Measured agreement of the correct field is
#: above 99%; the wrong one is below 50% even when the market is open.
MIN_FIELD_AGREEMENT = 0.5


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def price_matches_trusted_close(
    vendor_close: Decimal,
    trusted_close: Decimal,
) -> bool:
    """Decide whether a vendor close describes the trusted session.

    Both an absolute and a relative tolerance have to hold.  The absolute one
    absorbs the one-tick rounding difference between two vendors; the
    relative one stops a whole session of drift from passing on low-priced
    stocks, where a single tick is a large percentage.
    """
    diff = abs(vendor_close - trusted_close)
    if diff > PRICE_MATCH_TOLERANCE:
        return False
    if trusted_close == 0:
        return False
    return diff / abs(trusted_close) <= PRICE_MATCH_REL_TOLERANCE


@dataclass
class TencentValuationResult:
    """Outcome of one collection run."""

    trade_date: date
    requested: int = 0
    received: int = 0
    accepted: int = 0
    rejected: int = 0
    inserted: int = 0
    batch_id: int | None = None
    batch_no: str | None = None
    close_field: CloseField | None = None
    field_agreement: dict[str, float] = field(default_factory=dict)
    rejection_reasons: dict[str, int] = field(default_factory=dict)
    coverage_by_exchange: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_metadata(self) -> dict[str, object]:
        return {
            "target_trade_date": self.trade_date.isoformat(),
            "requested": self.requested,
            "received": self.received,
            "accepted": self.accepted,
            "rejected": self.rejected,
            "inserted": self.inserted,
            "batch_id": self.batch_id,
            "batch_no": self.batch_no,
            "close_field": self.close_field,
            "field_agreement": {
                key: round(value, 4) for key, value in self.field_agreement.items()
            },
            "rejection_reasons": self.rejection_reasons,
            "coverage_by_exchange": self.coverage_by_exchange,
            "source_name": "tencent",
        }


class TencentValuationCollectionService:
    """Collect Tencent valuation quotes and publish the verified rows."""

    def __init__(
        self,
        *,
        data_source: TencentQuoteDataSource | None = None,
        valuation_repository: ValuationSnapshotRepository | None = None,
        raw_repository: RawDailyBarImportRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.data_source = data_source or TencentQuoteDataSource()
        self.valuation_repository = valuation_repository or ValuationSnapshotRepository()
        self.raw_repository = raw_repository or RawDailyBarImportRepository()
        self.clock = clock or (lambda: datetime.now(SHANGHAI))

    # ----------------------------------------------------------------- helpers

    def _trusted_context(
        self, db: Session, *, trade_date: date
    ) -> tuple[dict[str, Decimal], dict[str, str], set[str]]:
        """Return the trusted closes, the exchange of each symbol and the open exchanges."""
        from sqlalchemy import select

        from app.models.market import DailyBar, Security, TradingCalendar

        closes = {
            row.symbol: row.close
            for row in db.scalars(
                select(DailyBar).where(
                    DailyBar.trade_date == trade_date,
                    DailyBar.adjust_type == "none",
                    DailyBar.security_type == "stock",
                    DailyBar.quality_status == "trusted",
                )
            )
            if row.close is not None
        }
        exchanges = {
            row.symbol: row.exchange
            for row in db.scalars(
                select(Security).where(
                    Security.security_type == "stock",
                    Security.is_active.is_(True),
                    Security.currency == "CNY",
                )
            )
        }
        open_exchanges = {
            row.exchange
            for row in db.scalars(
                select(TradingCalendar).where(
                    TradingCalendar.trade_date == trade_date,
                    TradingCalendar.is_open.is_(True),
                )
            )
        }
        return closes, exchanges, open_exchanges

    def _latest_completed_session(self, db: Session, *, before: date) -> date | None:
        """Return the latest trading session that is strictly before ``before``."""
        from sqlalchemy import select

        from app.models.market import TradingCalendar

        return db.scalar(
            select(TradingCalendar.trade_date)
            .where(
                TradingCalendar.exchange == "SH",
                TradingCalendar.trade_date < before,
                TradingCalendar.is_open.is_(True),
            )
            .order_by(TradingCalendar.trade_date.desc())
            .limit(1)
        )

    def _today_is_trading_day(self, db: Session, *, day: date) -> bool:
        """Whether ``day`` is an open session on the mainland exchanges.

        This decides which vendor field carries the observed close, because
        the vendor rolls its quote day at 09:15 on a trading day and stays on
        the previous session otherwise.  A missing calendar row is read as
        "not a trading day"; the agreement check in :meth:`run` then catches
        the mistake instead of letting it through.
        """
        from sqlalchemy import select

        from app.models.market import TradingCalendar

        return (
            db.scalar(
                select(TradingCalendar.trade_date)
                .where(
                    TradingCalendar.exchange == "SH",
                    TradingCalendar.trade_date == day,
                    TradingCalendar.is_open.is_(True),
                )
                .limit(1)
            )
            is not None
        )

    def _measure_field_agreement(
        self,
        quotes: dict[str, dict[str, object]],
        closes: dict[str, Decimal],
    ) -> dict[str, float]:
        """Measure how often each vendor field agrees with the trusted close.

        The correct field agrees with the trusted close of the observed
        session for essentially the whole universe; the wrong one agrees
        only where the two sessions happened to close at the same price.
        The gap is wide enough to separate the two even while the market is
        open, which is what makes the measurement usable as a guard.
        """
        agreement: dict[str, float] = {}
        for candidate in ("last_close", "price"):
            matched = 0
            considered = 0
            for code, payload in quotes.items():
                if not payload:
                    continue
                try:
                    symbol = to_symbol(code)
                except ValueError:
                    continue
                if symbol is None:
                    continue
                trusted = closes.get(symbol)
                if trusted is None:
                    continue
                vendor = _decimal(payload.get(candidate))
                if vendor is None:
                    continue
                considered += 1
                if price_matches_trusted_close(vendor, trusted):
                    matched += 1
            agreement[candidate] = matched / considered if considered else 0.0
        return agreement

    def _fail_batch(self, db: Session, job, exc: Exception, result: TencentValuationResult) -> None:
        if job.batch_id is None:
            return
        batch = db.get(ImportBatch, job.batch_id)
        if batch is None or batch.status != "running":
            return
        self.raw_repository.finish_batch(
            db,
            batch,
            status="failed",
            row_count=result.received,
            accepted_count=0,
            rejected_count=result.received,
            error_message=f"{type(exc).__name__}: {exc}",
        )

    # -------------------------------------------------------------------- run

    def run(self, db: Session, *, trade_date: date | None = None) -> TencentValuationResult:
        """Collect and publish valuation rows for the latest completed session.

        The observed session is the last trading session strictly before the
        current Shanghai calendar day: a day is only observed once it has
        fully passed, so a run at 21:00 still observes the previous day and
        only after midnight does the service move on.

        The vendor quote can only describe that one session.  Asking it for an
        older date yields rows that carry the newer date's close, so the
        target is pinned to it and a request for anything else is refused
        rather than silently misdated.

        Which vendor field carries the close is decided from the vendor's
        quote-day roll and then *checked* against the trusted close; a field
        that does not agree aborts the run instead of publishing rows dated
        to the wrong session.
        """
        local = self.clock().astimezone(SHANGHAI)
        today = local.date()

        # The source can only describe the latest completed session.  Pin the
        # target to it instead of trusting the caller: asking the vendor for
        # an older date returns rows that carry the newer date's close, and
        # no tolerance can tell the difference when the two prices are close.
        latest = self._latest_completed_session(db, before=today)
        if latest is None:
            raise ValueError("no completed trading session before the observation date")

        target = trade_date or latest
        if target >= today:
            raise ValueError(
                "valuation observation requires a completed trading day; "
                f"{target} is not before {today} in Shanghai time"
            )
        if target != latest:
            raise ValueError(
                "the vendor quote can only describe the latest completed session "
                f"({latest}); {target} is not reachable from a live quote"
            )

        # Which field holds the observed close depends on where the vendor's
        # quote day currently sits: it rolls at 09:15 on a trading day and
        # stays on the previous session otherwise.
        today_is_trading_day = self._today_is_trading_day(db, day=local.date())
        close_field = close_field_for_quote_day(local, today_is_trading_day=today_is_trading_day)

        closes, exchanges, open_exchanges = self._trusted_context(db, trade_date=target)
        if open_exchanges != {"SH", "SZ", "BJ"}:
            raise ValueError("target date is not a complete mainland trading session")
        if not closes:
            raise ValueError("no trusted unadjusted closes for the target date")

        eligible_symbols = {
            symbol
            for symbol, exchange in exchanges.items()
            if exchange in {"SH", "SZ", "BJ"} and symbol in closes
        }
        codes = sorted(symbol.split(".")[0] for symbol in eligible_symbols)

        result = TencentValuationResult(
            trade_date=target, requested=len(codes), close_field=close_field
        )
        with logged_job(
            db,
            job_name="tencent_valuation_collect",
            job_type="import",
            on_failure=lambda session, log, exc: self._fail_batch(session, log, exc, result),
            metadata={
                "target_trade_date": target.isoformat(),
                "source_name": "tencent",
                "close_field": close_field,
                "today_is_trading_day": today_is_trading_day,
            },
        ) as job:
            batch_no = f"tencent_val_{target:%Y%m%d}_{uuid4().hex[:12]}"
            batch = self.raw_repository.create_batch(
                db,
                batch_no=batch_no,
                source_name="tencent",
                source_type=self.data_source.source_type,
                dataset=DATASET,
                request_params={
                    "target_trade_date": target.isoformat(),
                    "requested_codes": len(codes),
                    "batch_size": self.data_source.batch_size,
                    "observed_at_utc": local.astimezone(UTC).isoformat(),
                    "code_version": settings.app_code_version,
                },
            )
            db.commit()
            batch_id = batch.id
            job.batch_id = batch_id
            db.commit()
            result.batch_id = batch_id
            result.batch_no = batch_no

            try:
                quotes = self.data_source.fetch_quotes(codes)
                result.received = len(quotes)
                fetched_at = self.clock().astimezone(UTC).isoformat()
                payload = {
                    "target_trade_date": target.isoformat(),
                    "fetched_at_utc": fetched_at,
                    "requested_codes": len(codes),
                    "received": len(quotes),
                    "quotes": quotes,
                }
                db.add(
                    RawVendorSnapshot(
                        batch_id=batch_id,
                        source_name="tencent",
                        dataset=DATASET,
                        snapshot_date=target,
                        raw_payload=payload,
                        row_hash=sha256(
                            dumps(payload, sort_keys=True, default=str).encode()
                        ).hexdigest(),
                    )
                )
                db.commit()

                # Check the field choice against the trusted close before
                # anything is published.  The vendor's quote-day roll is a
                # documented behaviour, not a guarantee, and the failure mode
                # of getting it wrong is silent: every row would carry a
                # plausible price for a different session.
                agreement = self._measure_field_agreement(quotes, closes)
                result.field_agreement = agreement
                if agreement[close_field] < MIN_FIELD_AGREEMENT:
                    raise ValueError(
                        "the vendor field "
                        f"'{close_field}' does not describe {target}: it agrees "
                        f"with the trusted close for only "
                        f"{agreement[close_field]:.1%} of the universe "
                        f"(last_close {agreement['last_close']:.1%}, "
                        f"price {agreement['price']:.1%}). Refusing to publish."
                    )

                rejections: Counter[str] = Counter()
                records = self.data_source.to_valuation_records(
                    quotes,
                    trade_date=target,
                    close_field=close_field,
                    allowed_symbols=eligible_symbols,
                    rejections=rejections,
                )
                for reason, count in rejections.items():
                    result.rejection_reasons[reason] = (
                        result.rejection_reasons.get(reason, 0) + count
                    )
                accepted_rows: list[dict[str, object]] = []
                coverage: dict[str, dict[str, int]] = {
                    exchange: {"expected": 0, "accepted": 0} for exchange in ("SH", "SZ", "BJ")
                }
                for symbol in eligible_symbols:
                    exchange = exchanges.get(symbol)
                    if exchange in coverage:
                        coverage[exchange]["expected"] += 1

                for record in records:
                    trusted_close = closes.get(record.symbol)
                    if trusted_close is None:
                        result.rejection_reasons["no_trusted_close"] = (
                            result.rejection_reasons.get("no_trusted_close", 0) + 1
                        )
                        continue
                    if not price_matches_trusted_close(record.close, trusted_close):
                        result.rejection_reasons["price_mismatch"] = (
                            result.rejection_reasons.get("price_mismatch", 0) + 1
                        )
                        continue
                    exchange = exchanges.get(record.symbol, "SZ")
                    accepted_rows.append(
                        {
                            "symbol": record.symbol,
                            "trade_date": target,
                            "close": record.close,
                            "total_market_cap": record.total_market_cap,
                            "float_market_cap": record.float_market_cap,
                            "pe_ttm": record.pe_ttm,
                            "pb": record.pb,
                            "ps_ttm": None,
                            "currency": "CNY",
                            "source_name": "tencent",
                            "raw_payload": {
                                **(record.raw_payload or {}),
                                "_research_batch_id": batch_id,
                                "_vendor": "tencent",
                                "_pe_caliber": "ttm",
                                "_static_pe": (
                                    str(record.pe_static) if record.pe_static is not None else None
                                ),
                                "_fetched_at_utc": fetched_at,
                                "_supplier_asof_verified": False,
                                "_close_field": close_field,
                                "_caliber": "restated_to_close",
                                "research_only": True,
                                "pit_verified": False,
                            },
                        }
                    )
                    if exchange in coverage:
                        coverage[exchange]["accepted"] += 1

                result.accepted = len(accepted_rows)
                result.rejected = result.received - result.accepted
                result.coverage_by_exchange = coverage

                inserted = self.valuation_repository.upsert_many(db, rows=accepted_rows)
                result.inserted = inserted

                batch = db.get(ImportBatch, batch_id)
                self.raw_repository.finish_batch(
                    db,
                    batch,
                    status="success",
                    row_count=result.received,
                    accepted_count=result.accepted,
                    rejected_count=result.rejected,
                )
                job.rows_read = result.received
                job.rows_inserted = inserted
                job.rows_failed = result.rejected
                job.metadata_ = {
                    **(job.metadata_ or {}),
                    **result.as_metadata(),
                    "price_match_tolerance": str(PRICE_MATCH_TOLERANCE),
                    "price_match_rel_tolerance": str(PRICE_MATCH_REL_TOLERANCE),
                    "min_field_agreement": MIN_FIELD_AGREEMENT,
                    "today_is_trading_day": today_is_trading_day,
                    "pit_verified": False,
                    "research_only": True,
                }
                db.commit()
            except Exception as exc:
                db.rollback()
                batch = db.get(ImportBatch, batch_id)
                if batch is not None and batch.status == "running":
                    self.raw_repository.finish_batch(
                        db,
                        batch,
                        status="failed",
                        row_count=result.received,
                        accepted_count=0,
                        rejected_count=result.received,
                        error_message=f"{type(exc).__name__}: {exc}",
                    )
                    db.commit()
                raise
        return result


__all__ = [
    "DATASET",
    "MIN_FIELD_AGREEMENT",
    "PRICE_MATCH_TOLERANCE",
    "TencentValuationCollectionService",
    "TencentValuationResult",
]
