from __future__ import annotations

import argparse
from datetime import date

from app.database.session import SessionLocal
from app.services.gap_backfill import MissingDailyBarBackfillService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan recent K-line gaps and backfill only missing or "
            "required-field-incomplete rows."
        )
    )
    parser.add_argument("--symbols", required=True, help="Comma-separated symbols.")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return a non-zero exit code unless the final status is success.",
    )
    args = parser.parse_args(argv)
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
    print(f"status={result.status}")
    print(f"missing_rows={result.missing_rows}")
    print(f"partial_rows={result.partial_rows}")
    print(f"suspect_rows={result.suspect_rows}")
    print(f"status_explained_rows={result.status_explained_rows}")
    print(f"lifecycle_unknown_rows={result.lifecycle_unknown_rows}")
    print(f"status_conflict_rows={result.status_conflict_rows}")
    print(f"unresolved_rows={result.unresolved_rows}")
    print(f"unresolved_actionable_rows={result.unresolved_actionable_rows}")
    print(f"skipped_unchanged_rows={result.skipped_unchanged_rows}")
    print(f"skipped_duplicate_rows={result.skipped_duplicate_rows}")
    print(f"ignored_in_scope_rows={result.ignored_in_scope_rows}")
    print(f"rejected_rows={result.rejected_rows}")
    print(f"conflicting_response_rows={result.conflicting_response_rows}")
    print(f"source_error_count={result.source_error_count}")
    return int(args.strict and result.status != "success")


if __name__ == "__main__":
    raise SystemExit(main())
