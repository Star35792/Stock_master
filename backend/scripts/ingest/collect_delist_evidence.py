"""Collect lifecycle evidence with source fallback into raw only."""

from __future__ import annotations

import argparse
import json

from app.database.session import SessionLocal, initialize_database
from app.services.delist_evidence import DelistEvidenceService
from app.utils.duckdb_write_lock import duckdb_write_lock


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("auto", "tushare", "akshare"), default="auto")
    parser.add_argument("--exchange", choices=("SH", "SZ"))
    args = parser.parse_args()
    if args.source == "auto" and not args.exchange:
        parser.error("--exchange is required for automatic fallback")
    with duckdb_write_lock():
        initialize_database()
        with SessionLocal() as db:
            service = DelistEvidenceService()
            result = (
                service.collect_with_fallback(db, exchange=args.exchange)
                if args.source == "auto" else
                service.collect(db, source=args.source, exchange=args.exchange)
            )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
