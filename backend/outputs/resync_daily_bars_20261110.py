"""One-off: re-sync the 2026-11-10 trusted daily bars after the close.

Why this exists
---------------
The last ``hithink_daily_bar`` batch for 2026-11-10 was created at
11:02:48 Shanghai time, i.e. during the morning session.  The bars it wrote
are intraday snapshots, not final closes: 927 securities end up with a vendor
close *above* the trusted bar's own ``high``, which is impossible for a
completed bar.  That is also why the Eastmoney valuation-history sweep for
11-10 failed its price gate (only 11.5% of closes matched).

Re-running the normal sync path after the close overwrites those rows through
the repository's ``ON CONFLICT DO UPDATE`` upsert.  No manual override, no
direct write to ``market``: this goes through the same raw -> market pipeline
and leaves the usual import-batch audit trail.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.repositories.market.security import SecurityRepository
from app.services.daily_sync import HithinkDailySyncService
from app.utils.duckdb_write_lock import duckdb_write_lock

TARGET = date(2026, 11, 10)


def _fingerprint() -> dict[str, object]:
    con = duckdb.connect(str(settings.metadata_db_path), read_only=True)
    try:
        row = con.execute(
            """
            SELECT count(*),
                   count(*) FILTER (WHERE close >= high - 0.005),
                   sum(close)
            FROM market.daily_bar
            WHERE trade_date = ? AND adjust_type = 'none'
            """,
            [TARGET],
        ).fetchone()
    finally:
        con.close()
    return {"rows": row[0], "close_at_high": row[1], "close_sum": str(row[2])}


def main() -> int:
    before = _fingerprint()
    print(json.dumps({"before": before}, ensure_ascii=False), flush=True)

    engine = create_engine(f"duckdb:///{settings.metadata_db_path}")
    try:
        with duckdb_write_lock("data/duckdb_write.lock", timeout_seconds=60.0):
            with Session(engine) as db:
                symbols = SecurityRepository().get_active_symbols(db)
                print(
                    json.dumps({"active_symbols": len(symbols)}, ensure_ascii=False),
                    flush=True,
                )
                result = HithinkDailySyncService().sync(
                    db,
                    symbols=symbols,
                    start_date=TARGET,
                    end_date=TARGET,
                    days=1,
                    adjust_type="none",
                )
                db.commit()
                print(
                    json.dumps(
                        {
                            "collection_status": result.collection.status,
                            "collected_rows": result.collection.row_count,
                            "rejected": result.collection.rejected_count,
                            "trusted_rows": result.market_build.trusted_count,
                            "market_rejected": result.market_build.rejected_count,
                            "batch_no": result.collection.batch_no,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
    finally:
        engine.dispose()

    after = _fingerprint()
    print(json.dumps({"after": after}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
