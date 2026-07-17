from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services.gap_backfill import MissingDailyBarBackfillService


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill only missing primary K-line dates.")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols.")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    with SessionLocal() as db:
        result = MissingDailyBarBackfillService().backfill(
            db,
            symbols=symbols,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    print(f"batch={result.batch_no}")
    print(f"expected_rows={result.expected_rows}")
    print(f"fetched_rows={result.fetched_rows}")
    print(f"inserted_rows={result.inserted_rows}")
    print(f"skipped_rows={result.skipped_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
