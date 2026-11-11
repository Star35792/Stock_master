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
* ``is_stale`` marks a quote that did not move from the previous close; such a
  row carries no fresh observation and is dropped rather than published.

The price field is a *live* quote, not a closing price
------------------------------------------------------
This is the single most important thing to know about this source, and it was
established by measurement rather than by reading the documentation.

``price`` is the **current** price at the moment of the request.  Measured on
2026-11-11 at 15:05 Shanghai time, ``price`` matched the 2026-11-11 intraday
bar for 39.7% of the market and the 2026-11-10 close for only 11.1%.

``last_close`` is the **previous session's close**.  Measured on the same run,
``last_close`` matched the 2026-11-10 trusted close for **5,561 of 5,573
symbols (99.78%)**.

So the usable field for a *historical* trade date is ``last_close``, and the
usable field for the *current* trade date is ``price`` -- but only after the
session has closed.  :meth:`TencentQuoteDataSource.to_valuation_records`
therefore takes the closing price from an explicitly chosen field, and the
caller has to decide which field that is.  Reading ``price`` for a past date
produces a snapshot of the wrong day, which the price gate then rejects for
almost the whole market -- a loud failure, but only because the gate is
there.

Two classes of symbol are rejected by that gate, and both are correct
rejections:

* **Index-code collisions.**  108 six-digit codes are index codes on one side
  while the vendor quotes a *company* under the same digits (``000905`` is the
  CSI 500 index and also a listed company).  The trusted close and the vendor
  quote differ by three orders of magnitude.
* **Ex-dividend symbols.**  For an ``XD`` symbol the vendor's ``last_close``
  is the *post*-ex-rights price while the trusted bar holds the
  *pre*-ex-rights close, so the two differ by the dividend amount.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
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
#: ``LAST_CLOSE`` is the previous session's close -- the right choice for a
#: historical trade date, because the quote is taken after that session ended.
#: ``PRICE`` is the live price -- only correct for a trade date whose session
#: has already closed.
CloseField = Literal["last_close", "price"]


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
        close_field: CloseField = "last_close",
        allowed_symbols: set[str] | None = None,
    ) -> list[ValuationRecord]:
        """Turn raw quote payloads into valuation records.

        ``close_field`` selects which vendor field carries the closing price of
        the observed trade date.  Use ``last_close`` for a historical date and
        ``price`` only for a session that has already closed -- see the module
        docstring for the measurement behind that choice.

        ``allowed_symbols`` restricts the result to the project's own symbol
        namespace.  The vendor quotes any six-digit code it knows, which
        includes index codes that collide with the stock code space (for
        example ``000905`` is both the CSI 500 index and a listed company).
        Filtering against the trusted security table keeps the collision out of
        the market layer instead of publishing a mismatched name and price.
        """
        records: list[ValuationRecord] = []
        for code, payload in quotes.items():
            if not payload:
                continue
            if payload.get("is_stale"):
                # A stale quote did not move from the previous close, so it
                # carries no fresh observation for this trade date.
                continue
            close = _decimal(payload.get(close_field))
            if close is None or close <= 0:
                continue
            try:
                symbol = _prefixed(code)
            except ValueError:
                continue
            if allowed_symbols is not None and symbol not in allowed_symbols:
                continue
            total_cap_yi = _decimal(payload.get("mcap_yi"))
            float_cap_yi = _decimal(payload.get("float_mcap_yi"))
            records.append(
                ValuationRecord(
                    symbol=symbol,
                    trade_date=trade_date,
                    close=close,
                    total_market_cap=total_cap_yi * _YI if total_cap_yi is not None else None,
                    float_market_cap=float_cap_yi * _YI if float_cap_yi is not None else None,
                    pe_ttm=_decimal(payload.get("pe_ttm")),
                    pe_static=_decimal(payload.get("pe_static")),
                    pb=_decimal(payload.get("pb")),
                    raw_payload=dict(payload),
                )
            )
        return records


__all__ = [
    "BATCH_SIZE",
    "CloseField",
    "TencentDataError",
    "TencentQuoteDataSource",
    "ValuationRecord",
]
