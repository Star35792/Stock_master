"""Collect and reconcile turnover evidence; never publish market or factor rows."""

from __future__ import annotations

import argparse
import json
from datetime import date

from app.database.session import SessionLocal, initialize_database
from app.services.turnover_evidence import TurnoverEvidenceService
from app.utils.duckdb_write_lock import duckdb_write_lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("auto", "tushare", "akshare"), default="auto")
    parser.add_argument("--trade-date", type=date.fromisoformat, required=True)
    parser.add_argument("--symbol", help="Required for AKShare")
    args = parser.parse_args()
    if args.source == "auto" and not args.symbol:
        parser.error("--symbol is required for automatic fallback")
    with duckdb_write_lock():
        initialize_database()
        with SessionLocal() as db:
            service = TurnoverEvidenceService()
            result = (
                service.collect_with_fallback(db, trade_date=args.trade_date, symbol=args.symbol)
                if args.source == "auto"
                else service.collect_day(
                    db,
                    source=args.source,
                    trade_date=args.trade_date,
                    symbol=args.symbol,
                )
            )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
