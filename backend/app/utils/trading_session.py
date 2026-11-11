"""A-share session clock: can a captured bar be treated as final?

Why this exists
---------------

A daily bar is only final *after* the closing auction.  A capture taken during
the session yields a partial bar: ``close`` is the last traded price at that
moment, and ``high``/``low``/``volume``/``amount`` only cover the elapsed part
of the day.  Publishing such a bar as ``trusted`` poisons everything
downstream, and it does so silently:

* the valuation anchor divides by a half-day ``close`` and a half-day ``EPS``;
* the Eastmoney valuation cross-check rejects the day (the vendor publishes the
  real close, the bar does not match) and the sweep quietly records zero
  covered days;
* the research screen keeps reporting a confident margin of safety computed
  from a 11:00 price.

On 2026-11-10 the ``hithink_daily_bar`` batch ran at 11:02 Shanghai time and
wrote intraday bars as ``trusted``.  The tell was 927 securities whose vendor
close sat *above* the bar's own ``high`` -- impossible for a completed session.

This module owns the single definition of "the session has settled" so the
market builder, the operations scripts and any future consumer cannot drift
apart.  It is intentionally tiny and dependency free.
"""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")

#: Continuous trading ends here.  A closing-auction print can land a little
#: later, so the settle threshold carries a deliberate buffer.
SESSION_CLOSE = time(15, 0)
SESSION_SETTLE = time(15, 30)


def session_settle_at(trade_date: date) -> datetime:
    """Return the Shanghai-local settle time that closes ``trade_date``'s session."""
    return datetime.combine(trade_date, SESSION_SETTLE, tzinfo=SHANGHAI)


def is_pre_close_capture(captured_at: datetime | None, trade_date: date) -> bool:
    """Whether a ``trade_date`` bar was captured before that session settled.

    ``captured_at`` is the moment the source row entered ``raw.daily_bar``
    (``raw.daily_bar.imported_at``), not the moment the market build ran: the
    build may happen hours later against a stale raw row.

    A missing timestamp is *not* evidence of an early capture.  The guard only
    disqualifies a bar it can positively place inside the session; a legacy row
    without provenance keeps its previous status rather than silently dropping
    out of every research read path.

    Naive timestamps are read as Shanghai wall-clock time, which is what the
    DuckDB ``now()`` server default produces on this host.
    """
    if captured_at is None:
        return False
    moment = captured_at if captured_at.tzinfo else captured_at.replace(tzinfo=SHANGHAI)
    return moment.astimezone(SHANGHAI) < session_settle_at(trade_date)
