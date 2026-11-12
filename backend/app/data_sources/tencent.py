"""Tencent quote adapter for valuation observations.

Why this source
---------------
``market.valuation_snapshot`` in the control plane has no ``pe_ttm`` at all:
the Eastmoney clist field ``f9`` is a *static* PE whose reporting period the
supplier does not certify, so the ingestion policy keeps it out of the generic
``pe_ttm`` column.  Measured against the whole market, Eastmoney ``f9`` equals
Tencent's ``pe_static`` for 99.98% of symbols, which proves both are static
PE rather than TTM.  Tencent's ``pe_ttm`` is a genuinely trailing-twelve-month
figure, so it is the field that can fill the gap.

This adapter therefore publishes two things the other sources cannot:

* ``pe_ttm`` -- a real trailing PE (Eastmoney's equivalent is static).
* an independent second opinion on ``pb`` and market capitalisation, which is
  what makes the valuation layer cross-checkable at all.

Vendor contract, measured on 2026-11-11
-----------------------------------------
* The quote endpoint takes a list of **bare six-digit codes** and answers with
  a mapping keyed by the same bare code.  Prefixes (``sh``/``sz``/``bj``) are
  rejected.
* A single request cannot carry the whole market: the URL length limit makes
  the vendor answer **HTTP 414 Request-URI Too Large**.  Batches of 60 codes
  are safe.
* ``mcap_yi`` and ``float_mcap_yi`` are in **hundred million yuan**; the
  control plane stores yuan.
* ``is_stale`` marks a quote that did not move from the previous close, i.e.
  the quote day had no trade at all.  Whether that kills the observation
  depends on the selected close field: on the ``price`` basis the quote day
  *is* the observed session, so the row is dropped; on the ``last_close``
  basis the previous session's real close is still there and the trusted
  gate decides.

The two price fields are relative to the vendor's *quote day*
-----------------------------------------------------------
The quote endpoint publishes two prices, and they are not two arbitrary
numbers: one is the quote day's price and the other is the close of the
session *before* the quote day.

* ``price`` is the quote day's price.  Outside the session it is that
  session's close, frozen; during the session it is the live tick.
* ``last_close`` is the close of the session before the quote day.

The quote day rolls at **09:15**, when the opening auction starts -- not at
midnight.  Measured on the same stock:

================  ==================  ==================
observation       ``price``           ``last_close``
================  ==================  ==================
09:03 (pre-open)  11-11 close         11-10 close
09:19 (auction)   live tick           11-11 close
15:24 (closed)    11-11 close         11-10 close
================  ==================  ==================

So *which field holds yesterday's close* depends on when the endpoint is
called, and the failure is silent: the wrong field still returns a
plausible price for every symbol, it just belongs to a different session.
Hard-coding one of the two is wrong for half of every day.  The caller has
to measure which field actually carries the session it wants -- see
``app/services/tencent_valuation_collection.py``.

Every derived quantity is quoted against the live price
-------------------------------------------------------
``mcap_yi``, ``float_mcap_yi``, ``pe_ttm``, ``pe_static`` and ``pb`` are all
computed from the quote day's price, not from ``last_close``.  Evidence:

* ``mcap / price`` recovers the share count exactly -- Ping An Bank gives
  19,405,902,500 shares against a true 19,405,920,000 -- while
  ``mcap / last_close`` is 0.17% off.
* ``price / pe_ttm`` is constant across observations (median drift
  0.0075%), i.e. the earnings per share is fixed and the PE moves with the
  price.  ``last_close / pe_ttm`` drifts by 0.6%.

The one exception is ``limit_up`` / ``limit_down``: they come from
``last_close`` (11.71 * 1.1 = 12.88), because the exchange sets the daily
limit from the previous close.

The consequence is that taking the close from ``last_close`` while copying
the market cap verbatim mixes two sessions: the close belongs to one and
the market cap to the next.  :meth:`TencentQuoteDataSource.to_valuation_records`
therefore rescales the price-basis quantities onto the close it selected, so
every published row is internally consistent.

Two classes of symbol are rejected by the price gate, and both are correct
rejections:

* **Index-code collisions.**  108 six-digit codes are index codes on the
  trusted side while the vendor quotes a company under the same digits
  (``000905`` is the CSI 500 index and also a listed company).
* **Ex-dividend symbols.**  For an ``XD`` symbol the vendor's
  ``last_close`` is the *post*-ex-rights price while the trusted bar holds
  the *pre*-ex-rights close.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Literal

_LIBS_ROOT = Path(__file__).resolve().parents[3] / "libs"
if str(_LIBS_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIBS_ROOT))

from a_stock_data.loader import load as _load_a_stock_data  # noqa: E402

#: Maximum number of codes per request.  The vendor builds the quote URL from
#: the code list, and a large enough list overflows the URI length limit and
#: comes back as HTTP 414.
BATCH_SIZE = 60

#: One hundred million yuan -- the unit the vendor reports market caps in.
_YI = Decimal("100000000")

#: The moment the vendor rolls its quote day.
#:
#: The opening auction starts at 09:15, and the endpoint switches to the new
#: session at that moment.  Before it, the endpoint still serves the previous
#: session: ``price`` is that session's frozen close and ``last_close`` the
#: one before it.  From 09:15 on, ``price`` becomes the live quote of the
#: current session and ``last_close`` carries the previous session's close.
QUOTE_DAY_ROLL = time(9, 15)

#: Decimal places kept for the published quantities.  The control plane stores
#: market caps with 6 decimals and the valuation ratios with 10.
_MARKET_CAP_PLACES = 6
_RATIO_PLACES = 10


class TencentDataError(RuntimeError):
    """Raised when the vendor quote cannot be turned into usable records."""


@dataclass(frozen=True)
class ValuationRecord:
    """One valuation observation for a single security."""

    symbol: str
    trade_date: date
    close: Decimal
    total_market_cap: Decimal | None = None
    float_market_cap: Decimal | None = None
    pe_ttm: Decimal | None = None
    pe_static: Decimal | None = None
    pb: Decimal | None = None
    raw_payload: dict[str, Any] | None = None


#: Which vendor field carries the closing price of the observed trade date.
#:
#: The two fields are relative to the vendor's *quote day*, and the quote day
#: rolls at 09:15 -- so which field holds the observed session's close
#: depends on when the endpoint is called.  Use
#: :func:`close_field_for_quote_day` to decide; never hard-code one.
CloseField = Literal["last_close", "price"]


def close_field_for_quote_day(now: datetime, *, today_is_trading_day: bool) -> CloseField:
    """Return the vendor field that carries the observed session's close.

    The observed session is always the last session *before* the current
    calendar day, so the vendor's quote day is either that session itself or
    the one right after it:

    * before 09:15 on a trading day, and on every non-trading day, the vendor
      still serves the previous session -- the quote day *is* the observed one
      and ``price`` carries its close;
    * from 09:15 on a trading day the vendor has rolled to the current
      session, so ``last_close`` carries the observed session's close.

    ``now`` must already be in the vendor's local time zone; the caller
    converts it, because the roll happens on the exchange's clock.
    """
    if today_is_trading_day and now.time().replace(tzinfo=None) >= QUOTE_DAY_ROLL:
        return "last_close"
    return "price"


def _restate(
    value: Decimal | None,
    *,
    close: Decimal,
    price_basis: Decimal,
    places: int,
) -> Decimal | None:
    """Move a price-basis quantity onto the selected close.

    The vendor quotes the market cap and the ratios against the quote day's
    price.  A share count and an earnings figure do not change within a
    session, so scaling them by ``close / price_basis`` puts every published
    quantity on the same session as the close.  Without this the row mixes
    two sessions: a close from one day and a market cap from the next.
    """
    if value is None:
        return None
    restated = value * close / price_basis
    return restated.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def _prefixed(symbol: str) -> str:
    """Map a bare six-digit code to the project's ``CODE.EXCHANGE`` symbol.

    The A-share code space is partitioned by leading digit:

    * ``6`` -- Shanghai main board and STAR market
    * ``0``/``2``/``3`` -- Shenzhen main board, SME board and ChiNext
    * ``4``/``8``/``9`` -- Beijing Stock Exchange (the ``92``/``83``/``87``
      prefixes used by the newer BSE listings included)
    """
    code = symbol.strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"not a six-digit A-share code: {symbol!r}")
    if code.startswith("6"):
        exchange = "SH"
    elif code.startswith(("0", "2", "3")):
        exchange = "SZ"
    elif code.startswith(("4", "8", "9")):
        exchange = "BJ"
    else:
        raise ValueError(f"unknown A-share code segment: {symbol!r}")
    return f"{code}.{exchange}"


def to_symbol(code: str) -> str | None:
    """Map a bare six-digit code to the project's symbol.

    Returns ``None`` for anything that is not an A-share code, so callers can
    skip the vendor's non-stock entries without catching exceptions.
    """
    try:
        return _prefixed(code)
    except ValueError:
        return None


class TencentQuoteDataSource:
    """Fetch valuation observations from the Tencent quote endpoint."""

    name = "tencent"
    source_type = "vendor_api"

    def __init__(self, *, batch_size: int = BATCH_SIZE) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.batch_size = batch_size
        self._module: Any | None = None

    # ------------------------------------------------------------------ fetch

    def _load(self) -> Any:
        if self._module is None:
            module, _skipped = _load_a_stock_data()
            self._module = module
        return self._module

    def fetch_quotes(self, codes: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch raw quote payloads for bare six-digit codes.

        Batches are issued sequentially so a single oversized request cannot
        abort the whole sweep, and a failed batch is reported rather than
        silently skipped.
        """
        module = self._load()
        cleaned = [code.strip() for code in codes if code and code.strip()]
        if not cleaned:
            return {}
        quotes: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(cleaned), self.batch_size):
            chunk = cleaned[offset : offset + self.batch_size]
            quotes.update(module.tencent_quote(chunk))
        return quotes

    # --------------------------------------------------------------- normalise

    def to_valuation_records(
        self,
        quotes: dict[str, dict[str, Any]],
        *,
        trade_date: date,
        close_field: CloseField,
        allowed_symbols: set[str] | None = None,
        rejections: Counter[str] | None = None,
    ) -> list[ValuationRecord]:
        """Turn raw quote payloads into valuation records.

        ``close_field`` selects which vendor field carries the closing price of
        the observed trade date.  It has no default on purpose: which field
        holds the close depends on the moment the quote was taken, and
        hard-coding one silently misdates half of every day.  Use
        :func:`close_field_for_quote_day` to pick it.

        Whatever field is selected, the market cap and the ratios are
        **restated onto it**.  The vendor quotes them against the quote day's
        price, so copying them verbatim next to a close from another session
        produces a row whose own numbers disagree with each other.

        ``allowed_symbols`` restricts the result to the project's own symbol
        namespace.  The vendor quotes any six-digit code it knows, which
        includes index codes that collide with the stock code space (for
        example ``000905`` is both the CSI 500 index and a listed company).
        Filtering against the trusted security table keeps the collision out of
        the market layer instead of publishing a mismatched name and price.

        ``rejections`` optionally collects why a quote was dropped, keyed by
        reason, so the caller can report the breakdown instead of a single
        opaque difference between received and accepted.
        """

        def drop(reason: str) -> None:
            if rejections is not None:
                rejections[reason] += 1

        records: list[ValuationRecord] = []
        for code, payload in quotes.items():
            if not payload:
                drop("empty_payload")
                continue
            if payload.get("is_stale") and close_field == "price":
                # The quote day *is* the observed session here, so a frozen
                # quote means the session itself had no trade: there is
                # nothing to observe.  On the ``last_close`` basis the frozen
                # quote still carries the previous session's real close -- and
                # because the price did not move, the restatement factor is
                # exactly 1 -- so it is kept and the trusted-close gate in
                # the service decides whether it belongs.
                drop("stale_quote")
                continue
            close = _decimal(payload.get(close_field))
            if close is None or close <= 0:
                drop("no_close")
                continue
            try:
                symbol = _prefixed(code)
            except ValueError:
                drop("not_an_a_share_code")
                continue
            if allowed_symbols is not None and symbol not in allowed_symbols:
                drop("outside_trusted_universe")
                continue
            price_basis = _decimal(payload.get("price"))
            if price_basis is None or price_basis <= 0:
                # Without the quote day's price the price-basis quantities
                # cannot be placed on the selected close, and the row would
                # mix two sessions.
                drop("no_price_basis")
                continue
            total_cap_yi = _decimal(payload.get("mcap_yi"))
            float_cap_yi = _decimal(payload.get("float_mcap_yi"))
            records.append(
                ValuationRecord(
                    symbol=symbol,
                    trade_date=trade_date,
                    close=close,
                    total_market_cap=(
                        _restate(
                            total_cap_yi * _YI if total_cap_yi is not None else None,
                            close=close,
                            price_basis=price_basis,
                            places=_MARKET_CAP_PLACES,
                        )
                    ),
                    float_market_cap=(
                        _restate(
                            float_cap_yi * _YI if float_cap_yi is not None else None,
                            close=close,
                            price_basis=price_basis,
                            places=_MARKET_CAP_PLACES,
                        )
                    ),
                    pe_ttm=_restate(
                        _decimal(payload.get("pe_ttm")),
                        close=close,
                        price_basis=price_basis,
                        places=_RATIO_PLACES,
                    ),
                    pe_static=_restate(
                        _decimal(payload.get("pe_static")),
                        close=close,
                        price_basis=price_basis,
                        places=_RATIO_PLACES,
                    ),
                    pb=_restate(
                        _decimal(payload.get("pb")),
                        close=close,
                        price_basis=price_basis,
                        places=_RATIO_PLACES,
                    ),
                    raw_payload={
                        **payload,
                        "_close_field": close_field,
                        "_quote_day_price": str(price_basis),
                        "_caliber": "restated_to_close",
                    },
                )
            )
        return records


__all__ = [
    "BATCH_SIZE",
    "CloseField",
    "QUOTE_DAY_ROLL",
    "TencentDataError",
    "TencentQuoteDataSource",
    "ValuationRecord",
    "close_field_for_quote_day",
    "to_symbol",
]
