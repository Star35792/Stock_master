"""Collect dividend evidence with source fallback into raw only."""

from __future__ import annotations

import argparse
import json

from app.database.session import SessionLocal, initialize_database
from app.services.corporate_action_evidence import CorporateActionEvidenceService
from app.utils.duckdb_write_lock import duckdb_write_lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", choices=("auto", "akshare", "tushare", "eastmoney"), default="auto"
    )
    parser.add_argument("--symbol", help="A-share symbol, e.g. 600519.SH; omit for pending queue")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")
    with duckdb_write_lock():
        initialize_database()
        with SessionLocal() as db:
            service = CorporateActionEvidenceService()
            symbols = [args.symbol] if args.symbol else service.pending_symbols(
                db, source="akshare" if args.source == "auto" else args.source,
                limit=args.limit,
            )
            outcomes = []
            for symbol in symbols:
                if args.source == "auto":
                    outcomes.append(service.collect_with_fallback(db, symbol=symbol))
                else:
                    outcomes.append(service.collect_one(db, source=args.source, symbol=symbol))
    print(json.dumps(outcomes, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
