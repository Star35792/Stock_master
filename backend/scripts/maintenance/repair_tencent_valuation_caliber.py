"""Repair the Tencent valuation rows whose numbers mix two sessions.

What went wrong
---------------
The Tencent quote endpoint publishes two prices, both relative to its *quote
day*: ``price`` is the quote day's price and ``last_close`` the close of the
session before it.  The quote day rolls at 09:15, so which field carries a
given session's close depends on the moment the endpoint is called.

The collection service used to hard-code ``last_close``.  Two batches
published before the fix are affected, in two different ways:

* **2026-11-11, 435 rows** -- the batch was fetched at 09:03, before the
  roll, so the quote day was still 2026-11-11 and ``price`` held that
  session's close.  Reading ``last_close`` published the **2026-11-10**
  close under the 2026-11-11 label: 434 of the 435 rows were misdated.
* **2026-11-10, 5,561 rows** -- the batch was fetched at 15:24, after the
  roll, so the quote day was 2026-11-11 and ``last_close`` did carry the
  2026-11-10 close.  The dates are right, but the market cap and the ratios
  were copied verbatim from the vendor and are quoted against the quote day's
  price: each row states a close from one session and a market cap from the
  next.

What this script does
----------------------
Two independent actions, both defaulting to a dry run:

``--action misdated``
    Deletes the rows whose close belongs to a session other than the one
    their ``trade_date`` names.  Those rows cannot be repaired in place --
    the date itself is wrong -- and the source can only describe its latest
    session, so the fix is to drop them and re-collect.

``--action caliber``
    Restates the market cap and the ratios of the rows whose date is right
    but whose numbers mix sessions.  The restatement is recomputed **from the
    raw vendor payload**, not from the stored values, so the script does not
    depend on whatever a previous run wrote.  Every rewritten row is then
    checked for the one property that must hold: the market cap divided by
    the close still implies the same share count.

Safety design
--------------
* The dry run is the default; ``--apply`` is needed to write.
* Rows are only touched when the trusted bar confirms the session they
  belong to.  A row that cannot be placed is counted and left alone.
* The restatement is idempotent: a row that already carries the correct
  numbers is recognised by comparing the recomputed value with the stored
  one, so a second run changes nothing.

Usage
-----
    python -m scripts.maintenance.repair_tencent_valuation_caliber \
        --action misdated --trade-date 2026-11-11
    python -m scripts.maintenance.repair_tencent_valuation_caliber \
        --action misdated --trade-date 2026-11-11 --apply
    python -m scripts.maintenance.repair_tencent_valuation_caliber \
        --action caliber --trade-date 2026-11-10 --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.settings import settings  # noqa: E402
from app.database.session import engine  # noqa: E402
from app.models.market import DailyBar, TradingCalendar, ValuationSnapshot  # noqa: E402

SOURCE = "tencent"

#: One hundred million yuan -- the unit the vendor reports market caps in.
_YI = Decimal("100000000")

#: Decimal places the control plane keeps.
_MARKET_CAP_PLACES = 6
_RATIO_PLACES = 10

#: How far a vendor price may sit from the trusted close and still count as
#: the same session.
#:
#: It has to stay **below one minimum tick**.  A-share prices tick in 0.01,
#: so a tolerance of 0.01 or more lets the close of one session match the
#: close of the next whenever the two differ by a single tick -- and then
#: "which session does this row belong to" cannot be answered at all.  Half a
#: tick absorbs the two vendors' rounding without admitting another day.
_PRICE_TOLERANCE = Decimal("0.005")

#: How many sessions back the misdating check looks for the row's real close.
_LOOKBACK_SESSIONS = 5

#: Relative tolerance for the share-count check.
_SHARE_TOLERANCE = Decimal("0.000001")

#: Relative tolerance for "this row already holds the recomputed numbers".
#:
#: It only has to absorb the rounding of the last digit, so it is orders of
#: magnitude tighter than the share-count check -- a row that still carries
#: the wrong caliber differs by the price move, not by a rounding.
_EQUALITY_TOLERANCE = Decimal("0.000000001")


def _decimal(value: object) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value))
    except Exception:
        return None
    return number if number.is_finite() else None


def _quantize(value: Decimal | None, places: int) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def _sessions(db: Session, *, before: date | None = None, after: date | None = None) -> list[date]:
    query = select(TradingCalendar.trade_date).where(
        TradingCalendar.exchange == "SH",
        TradingCalendar.is_open.is_(True),
    )
    if before is not None:
        query = query.where(TradingCalendar.trade_date < before)
    if after is not None:
        query = query.where(TradingCalendar.trade_date > after)
    return list(db.scalars(query.order_by(TradingCalendar.trade_date)))


def _trusted_closes(db: Session, session: date) -> dict[str, Decimal]:
    return {
        row.symbol: row.close
        for row in db.scalars(
            select(DailyBar).where(
                DailyBar.trade_date == session,
                DailyBar.adjust_type == "none",
                DailyBar.security_type == "stock",
                DailyBar.quality_status == "trusted",
            )
        )
        if row.close is not None
    }


def _matches(value: Decimal | None, trusted: Decimal | None) -> bool:
    if value is None or trusted is None:
        return False
    return abs(value - trusted) <= _PRICE_TOLERANCE


def _same(stored: Decimal | None, expected: Decimal | None) -> bool:
    """Whether a stored quantity already equals the recomputed one.

    The comparison is relative rather than exact: the last digit of a
    division is a rounding, and a run that rewrites it would make the
    script non-idempotent without changing anything.
    """
    if stored is None or expected is None:
        return stored is None and expected is None
    if expected == 0:
        return stored == 0
    return abs(stored - expected) / abs(expected) <= _EQUALITY_TOLERANCE


def _tencent_rows(db: Session, session: date) -> list[ValuationSnapshot]:
    return list(
        db.scalars(
            select(ValuationSnapshot).where(
                ValuationSnapshot.source_name == SOURCE,
                ValuationSnapshot.trade_date == session,
            )
        )
    )


# --------------------------------------------------------------------- actions


def _action_misdated(db: Session, *, session: date, apply: bool) -> dict[str, int]:
    """Delete the rows whose close belongs to a session other than ``session``."""
    stats = {"examined": 0, "misdated": 0, "deleted": 0, "kept": 0}
    trusted_now = _trusted_closes(db, session)
    earlier = _sessions(db, before=session)[-_LOOKBACK_SESSIONS:]
    earlier_closes = {day: _trusted_closes(db, day) for day in earlier}

    doomed: list[ValuationSnapshot] = []
    for row in _tencent_rows(db, session):
        stats["examined"] += 1
        if row.close is None:
            stats["kept"] += 1
            continue
        if _matches(row.close, trusted_now.get(row.symbol)):
            stats["kept"] += 1
            continue
        # The close belongs to an earlier session: the row is misdated.
        for closes in earlier_closes.values():
            if _matches(row.close, closes.get(row.symbol)):
                stats["misdated"] += 1
                doomed.append(row)
                break
        else:
            stats["kept"] += 1

    if apply and doomed:
        db.execute(
            delete(ValuationSnapshot).where(
                ValuationSnapshot.source_name == SOURCE,
                ValuationSnapshot.trade_date == session,
                ValuationSnapshot.symbol.in_([row.symbol for row in doomed]),
            )
        )
        db.commit()
        stats["deleted"] = len(doomed)
    return stats


def _action_caliber(db: Session, *, session: date, apply: bool) -> dict[str, int]:
    """Restate the market cap and the ratios onto the row's own close."""
    stats = {
        "examined": 0,
        "repaired": 0,
        "already_correct": 0,
        "skipped": 0,
        "share_check_failed": 0,
        "restated_without_next_bar": 0,
    }
    next_session = next(iter(_sessions(db, after=session)), None)
    if next_session is None:
        raise SystemExit(f"no session after {session}; cannot locate the quote day")
    trusted_now = _trusted_closes(db, session)
    trusted_next = _trusted_closes(db, next_session)

    for row in _tencent_rows(db, session):
        stats["examined"] += 1
        payload = row.raw_payload if isinstance(row.raw_payload, dict) else None
        if not payload:
            stats["skipped"] += 1
            continue
        # The row has to belong to the session it claims.
        if not _matches(row.close, trusted_now.get(row.symbol)):
            stats["skipped"] += 1
            continue
        price = _decimal(payload.get("price"))
        if price is None or price <= 0:
            stats["skipped"] += 1
            continue
        # The vendor's market cap is ``shares * price``, so scaling it by
        # ``close / price`` yields ``shares * close`` -- an identity that
        # holds whatever session the price belongs to.  The next session's
        # bar confirms the quote day when it exists; a stock that was
        # suspended on that day has no bar at all, and the identity still
        # holds, so it is restated rather than skipped.
        next_close = trusted_next.get(row.symbol)
        if next_close is None:
            stats["restated_without_next_bar"] += 1
        elif not _matches(price, next_close):
            # The quote day is not the session after this one, so the row
            # was collected in a different state of the vendor's clock and
            # the stored numbers cannot be placed.
            stats["skipped"] += 1
            continue

        raw_cap_yi = _decimal(payload.get("mcap_yi"))
        raw_float_cap_yi = _decimal(payload.get("float_mcap_yi"))
        raw_pe = _decimal(payload.get("pe_ttm"))
        raw_pb = _decimal(payload.get("pb"))
        if raw_cap_yi is None or row.close is None:
            stats["skipped"] += 1
            continue

        # Scale by ``close / price`` in one step.  Folding the division into
        # the multiplication keeps a single rounding, and dividing by the
        # factor first would leave a rounding of its own in the last digit.
        new_cap = _quantize(
            raw_cap_yi * _YI * row.close / price if raw_cap_yi is not None else None,
            _MARKET_CAP_PLACES,
        )
        new_float_cap = _quantize(
            raw_float_cap_yi * _YI * row.close / price if raw_float_cap_yi is not None else None,
            _MARKET_CAP_PLACES,
        )
        new_pe = _quantize(
            raw_pe * row.close / price if raw_pe is not None else None, _RATIO_PLACES
        )
        new_pb = _quantize(
            raw_pb * row.close / price if raw_pb is not None else None, _RATIO_PLACES
        )

        # The share count has to survive the restatement.  This is the one
        # property that distinguishes a correct restatement from a plausible
        # but wrong number.
        vendor_shares = raw_cap_yi * _YI / price
        published_shares = new_cap / row.close
        if abs(published_shares - vendor_shares) / vendor_shares > _SHARE_TOLERANCE:
            stats["share_check_failed"] += 1
            continue

        already = (
            _same(row.total_market_cap, new_cap)
            and _same(row.float_market_cap, new_float_cap)
            and _same(row.pe_ttm, new_pe)
            and _same(row.pb, new_pb)
        )
        if already:
            stats["already_correct"] += 1
            continue

        stats["repaired"] += 1
        if apply:
            row.total_market_cap = new_cap
            row.float_market_cap = new_float_cap
            row.pe_ttm = new_pe
            row.pb = new_pb
            raw_payload = dict(payload)
            raw_payload["_close_field"] = "last_close"
            raw_payload["_quote_day_price"] = str(price)
            raw_payload["_caliber"] = "restated_to_close"
            raw_payload["_repaired_at"] = date.today().isoformat()
            row.raw_payload = raw_payload

    if apply:
        db.commit()
    return stats


_ACTIONS = {"misdated": _action_misdated, "caliber": _action_caliber}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Repair the Tencent valuation rows affected by the quote-day mix-up",
    )
    parser.add_argument(
        "--action",
        choices=sorted(_ACTIONS),
        required=True,
        help="misdated: drop rows dated to the wrong session; caliber: restate the numbers",
    )
    parser.add_argument("--trade-date", required=True, help="the session to repair (YYYY-MM-DD)")
    parser.add_argument("--db", help="override the control-plane database path")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (the default is a dry run)",
    )
    args = parser.parse_args()

    session = date.fromisoformat(args.trade_date)
    db_path = Path(args.db or settings.metadata_db_path)

    if args.db:
        from sqlalchemy import create_engine

        bind = create_engine(f"duckdb:///{db_path}")
    else:
        bind = engine

    print("=== 腾讯估值口径修复 ===")
    print(f"动作: {args.action}")
    print(f"目标交易日: {session}")
    print(f"模式: {'写入' if args.apply else '演练(加 --apply 生效)'}")
    print(f"数据库: {db_path}")

    with Session(bind) as db:
        stats = _ACTIONS[args.action](db, session=session, apply=args.apply)

    print("\n=== 结果 ===")
    for key, value in stats.items():
        print(f"{key:20s}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
